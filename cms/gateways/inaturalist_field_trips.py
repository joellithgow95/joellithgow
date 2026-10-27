"""
iNaturalist Field Trips gateway — one CMS document per outing.

Legacy (ADR 026 in astraeus): ``inaturalist_observations`` writes one data document
per sighting instead. This gateway keeps the outing posts current until the
migration turns them into posts that slice those sightings, and is removed then.
Its pure helpers (``curate_observation``, ``photo_urls``, ``taxon_tags``) are shared
with the new gateway and move there when this module goes.

An outing is one observed date at one place: observations from the same day that
sit within ``cluster_radius_m`` of each other (chained, so a trail with a sighting
every few hundred metres is one outing). Two places in one day are two outings.

What it stores is what the site shows — a count, species names, photo URLs, a
bounding box and a slim record per observation — not iNaturalist's raw payload,
which is ~35 KB per observation and changes whenever anyone comments or faves.

Ownership. ``owned_fields`` are machine-sourced and refreshed on every sync,
including ``tags`` (the taxon groups seen), so adding a bird to an outing later
adds ``birds``. The accepted cost: a hand edit to an outing's tags is overwritten
by the next sync that changes the outing. Everything else (title, place, publish
date) is seeded when the outing is first created and then belongs to whoever
edits it in the CMS.

Ranges (``self.range``, see ``BaseGateway.sync``):
    since_last_sync  ask iNat for observations changed since the cursor
                     (``updated_since``), then re-fetch the whole day for each
                     date touched so the document always holds the full outing.
    all_time         every observation.
    custom           observations *observed* between the given dates.

An outing a run leaves alone (a person's draft is on it) or fails on is reported
in the sync reply and not remembered: an incremental run only meets it again if
iNaturalist changes it, so catch it up with an ``all_time`` run.

An observation deleted at iNaturalist, or one whose date was edited, is only
noticed by ``all_time`` (an edited date is also caught incrementally). A
document is never deleted by a sync.

Environment variables:
    INATURALIST_USERNAME        iNaturalist username to fetch observations for
    INATURALIST_OUTING_RADIUS_M Override the outing radius in metres (default 3000)
"""

from __future__ import annotations

import collections
import math
import os
from collections.abc import AsyncIterator, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from starlette_cms_gateways import BaseGateway, GatewayItem

_INAT_API = "https://api.inaturalist.org/v1"
_PAGE_SIZE = 200
# 3 km: on the real data only 2026-07-04 (Oakland, then Albany) splits. At 1 km the
# 2026-05-25 and 2026-09-05 hikes, whose sightings are spread along a trail, split
# wrongly too.
DEFAULT_RADIUS_M = 3000.0


def outing_radius_m() -> float:
    """The radius outings are clustered at: ``INATURALIST_OUTING_RADIUS_M`` or the default.

    Everything that clusters (the gateway, the migration, the report) reads it here.
    """
    return float(os.environ.get("INATURALIST_OUTING_RADIUS_M") or DEFAULT_RADIUS_M)

# Maps iNaturalist iconic_taxon_name → friendly tag
_TAXON_TAGS: dict[str, str] = {
    "Aves": "birds",
    "Plantae": "plants",
    "Insecta": "insects",
    "Fungi": "fungi",
    "Mammalia": "mammals",
    "Reptilia": "reptiles",
    "Amphibia": "amphibians",
    "Arachnida": "spiders",
    "Mollusca": "mollusks",
    "Animalia": "animals",
    "Actinopterygii": "fish",
    "Chromista": "algae",
}

LEGACY_REF_PREFIX = "inaturalist:outing:"
LEGACY_SLUG_PREFIX = "nature-outing-"


# ---------------------------------------------------------------------------
# Pure helpers — no network, no CMS. Unit-tested directly.
# ---------------------------------------------------------------------------


def observation_coords(obs: dict[str, Any]) -> tuple[float, float] | None:
    """
    ``(lat, lon)`` of an observation, or ``None`` when it has no usable position.

    iNaturalist's v1 API gives ``location: "lat,lon"``. The older top-level
    ``latitude``/``longitude`` and our own curated ``lat``/``lon`` are accepted too. An *obscured*
    observation (a sensitive taxon) carries a position fuzzed over ~20 km, which
    says nothing about where the outing was, so it counts as unlocated.
    """
    if obs.get("obscured"):
        return None
    lat = lon = None
    loc = obs.get("location")
    if isinstance(loc, str) and "," in loc:
        a, _, b = loc.partition(",")
        lat, lon = a, b
    elif obs.get("latitude") is not None and obs.get("longitude") is not None:
        lat, lon = obs["latitude"], obs["longitude"]
    elif obs.get("lat") is not None and obs.get("lon") is not None:  # our own curated shape
        lat, lon = obs["lat"], obs["lon"]
    try:
        la, lo = float(lat), float(lon)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not (-90 <= la <= 90 and -180 <= lo <= 180):
        return None
    return la, lo


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in metres between two ``(lat, lon)`` points."""
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def _observed_at(obs: dict[str, Any]) -> datetime | None:
    raw = obs.get("time_observed_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _sort_key(obs: dict[str, Any]) -> tuple[str, int]:
    return (obs.get("time_observed_at") or "", int(obs.get("id") or 0))


def is_curated(obs: dict[str, Any]) -> bool:
    """True for a record produced by :func:`curate_observation`, False for iNaturalist's raw shape."""
    return "taxon_name" in obs


def curate_observation(obs: dict[str, Any]) -> dict[str, Any]:
    """The slim record stored per observation. The site only counts these; the
    rest is for the CMS reader and for matching a document back to its
    observations."""
    taxon = obs.get("taxon") or {}
    coords = observation_coords(obs)
    out: dict[str, Any] = {
        "id": obs.get("id"),
        "uri": obs.get("uri") or "",
        "observed_on": obs.get("observed_on") or "",
        "time_observed_at": obs.get("time_observed_at") or "",
        "taxon_name": taxon.get("name") or "",
        "common_name": taxon.get("preferred_common_name") or "",
        "iconic_taxon": taxon.get("iconic_taxon_name") or "",
        "place_guess": obs.get("place_guess") or "",
        # No quality_grade: community IDs change it, which would republish a post
        # for something the site never shows.
    }
    if coords:
        out["lat"], out["lon"] = coords
    return out


def cluster_observations(
    observations: Iterable[dict[str, Any]], radius_m: float | None = None
) -> list[list[dict[str, Any]]]:
    """
    Split one day's observations into outings.

    *radius_m* defaults to :func:`outing_radius_m`, so every caller that does not
    pass one clusters at the configured radius.

    Located observations are linked when within *radius_m* of each other
    (single linkage, so a long trail is one outing). Unlocated ones — no
    coordinates, or obscured — join the cluster that shares their place name,
    else the nearest in time, else the first. With nothing located at all the
    day is one outing. Clusters come back earliest-first, members in time order.
    """
    if radius_m is None:
        radius_m = outing_radius_m()
    obs_list = sorted(observations, key=_sort_key)
    located = [(o, c) for o in obs_list if (c := observation_coords(o))]
    unlocated = [o for o in obs_list if not observation_coords(o)]

    # Union-find over located observations.
    parent = list(range(len(located)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(located)):
        for j in range(i + 1, len(located)):
            if haversine_m(located[i][1], located[j][1]) <= radius_m:
                parent[find(i)] = find(j)

    groups: dict[int, list[dict[str, Any]]] = collections.defaultdict(list)
    for i, (o, _) in enumerate(located):
        groups[find(i)].append(o)
    clusters = list(groups.values())

    if not clusters:
        # Nothing to measure. place_guess strings vary within one hike, so splitting
        # on them would break outings up; with no positions the day stays one outing.
        clusters = [list(unlocated)] if unlocated else []
    else:
        for o in unlocated:
            place = o.get("place_guess") or ""
            target = next(
                (c for c in clusters if place and any(m.get("place_guess") == place for m in c)),
                None,
            )
            if target is None:
                t = _observed_at(o)
                if t is not None:
                    timed = [
                        (abs((_observed_at(m) - t).total_seconds()), idx)  # type: ignore[operator]
                        for idx, c in enumerate(clusters)
                        for m in c
                        if _observed_at(m) is not None
                    ]
                    if timed:
                        target = clusters[min(timed)[1]]
            (target if target is not None else clusters[0]).append(o)

    for c in clusters:
        c.sort(key=_sort_key)
    clusters.sort(key=lambda c: _sort_key(c[0]))
    return clusters


@dataclass(frozen=True)
class ExistingOuting:
    """What an already-synced outing document tells us about itself."""

    import_ref: str
    observation_ids: frozenset[int]


def outing_key(day: str, n: int) -> tuple[str, str]:
    """``(import_ref, slug)`` of the *n*-th outing of *day*. The first keeps the
    pre-clustering ref and slug, so existing links and documents carry over."""
    if n == 1:
        return f"{LEGACY_REF_PREFIX}{day}", f"{LEGACY_SLUG_PREFIX}{day}"
    return f"{LEGACY_REF_PREFIX}{day}:{n}", f"{LEGACY_SLUG_PREFIX}{day}-{n}"


def assign_outing_keys(
    day: str,
    clusters: list[list[dict[str, Any]]],
    existing: list[ExistingOuting],
) -> list[tuple[str, str]]:
    """
    Give each cluster the ``(import_ref, slug)`` of the existing document that
    already holds most of its observations, so adding a sighting to an outing
    updates that outing and never moves it to another document.

    Clusters with no match get the first ref nobody holds: the day's base ref if
    free, else ``:2``, ``:3``… A ref held by a document that matches no cluster
    (its observations were deleted or moved) stays reserved, never reused.
    """
    cluster_ids = [{o["id"] for o in c if o.get("id") is not None} for c in clusters]
    pairs = sorted(
        (
            (-len(ids & ex.observation_ids), ci, ei)
            for ci, ids in enumerate(cluster_ids)
            for ei, ex in enumerate(existing)
            if ids & ex.observation_ids
        )
    )
    keys: dict[int, str] = {}
    taken: set[int] = set()
    for _, ci, ei in pairs:
        if ci not in keys and ei not in taken:
            keys[ci] = existing[ei].import_ref
            taken.add(ei)

    reserved = {ex.import_ref for ex in existing} | set(keys.values())
    n = 1
    for ci in range(len(clusters)):
        if ci in keys:
            continue
        while outing_key(day, n)[0] in reserved:
            n += 1
        keys[ci] = outing_key(day, n)[0]
        reserved.add(keys[ci])

    return [(keys[ci], _slug_for(day, keys[ci])) for ci in range(len(clusters))]


def _slug_for(day: str, import_ref: str) -> str:
    suffix = import_ref.removeprefix(f"{LEGACY_REF_PREFIX}{day}")
    return f"{LEGACY_SLUG_PREFIX}{day}" + (f"-{suffix[1:]}" if suffix.startswith(":") else "")


def dominant_place(obs_group: list[dict[str, Any]]) -> str:
    """The most common ``place_guess`` in the group (earliest wins a tie)."""
    counts = collections.Counter(o.get("place_guess") for o in obs_group if o.get("place_guess"))
    if not counts:
        return ""
    best = max(counts.values())
    return next(o["place_guess"] for o in obs_group if counts.get(o.get("place_guess")) == best)


def unique_species(obs_group: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    names: list[str] = []
    for obs in obs_group:
        taxon = obs.get("taxon") or {}
        name = taxon.get("preferred_common_name") or taxon.get("name") or ""
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def photo_urls(obs_group: list[dict[str, Any]]) -> list[str]:
    urls: list[str] = []
    for obs in obs_group:
        for photo in obs.get("photos") or []:
            url = photo.get("url") or ""
            if url:
                urls.append(url.replace("square", "original"))
    return urls


def taxon_tags(obs_group: list[dict[str, Any]]) -> list[str]:
    seen = {
        _TAXON_TAGS[iconic]
        for obs in obs_group
        if (iconic := (obs.get("taxon") or {}).get("iconic_taxon_name") or "") in _TAXON_TAGS
    }
    return sorted(seen)


def bounding_box(obs_group: list[dict[str, Any]]) -> dict[str, float] | None:
    coords = [c for o in obs_group if (c := observation_coords(o))]
    if not coords:
        return None
    lats, lons = [c[0] for c in coords], [c[1] for c in coords]
    return {
        "lat_min": min(lats),
        "lat_max": max(lats),
        "lon_min": min(lons),
        "lon_max": max(lons),
    }


def build_outing_item(
    day: str, import_ref: str, slug: str, obs_group: list[dict[str, Any]]
) -> GatewayItem:
    place = dominant_place(obs_group)
    title = place if place else day
    return GatewayItem(
        import_ref=import_ref,
        slug=slug,
        title=title,
        body={
            # Seeded once, then the editor's:
            "title": title,
            "publish_date": day,
            "outing_date": day,
            "place_guess": place,
            # Owned by the gateway (see INaturalistFieldTripsGateway.owned_fields):
            "tags": taxon_tags(obs_group),
            "observation_count": float(len(obs_group)),
            "species_list": unique_species(obs_group),
            "observations": [curate_observation(o) for o in obs_group],
            "photo_urls": photo_urls(obs_group),
            "bounding_box": bounding_box(obs_group),
        },
    )


def existing_outing(doc: dict[str, Any]) -> tuple[str, ExistingOuting] | None:
    """``(outing_date, ExistingOuting)`` for a stored outing document."""
    body = doc.get("body") or {}
    day = body.get("outing_date") or body.get("publish_date")
    ref = doc.get("import_ref")
    if not day or not ref:
        return None
    ids = frozenset(
        int(o["id"]) for o in body.get("observations") or [] if isinstance(o, dict) and o.get("id")
    )
    return day, ExistingOuting(ref, ids)


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------


class INaturalistFieldTripsGateway(BaseGateway):
    """Legacy: one post per outing. Still keeps the live outing posts current, so run it
    alongside inaturalist-observations (one data document per sighting, ADR 026) until
    the migration converts those posts and removes it."""

    service_name = "inaturalist_field_trips"
    block_type = "inaturalist_outing"
    auto_publish = True
    default_range = "since_last_sync"
    owned_fields = (
        "observation_count",
        "species_list",
        "observations",
        "photo_urls",
        "bounding_box",
        "tags",
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._username = os.environ["INATURALIST_USERNAME"]
        self.cluster_radius_m = outing_radius_m()

    async def fetch(self) -> AsyncIterator[GatewayItem]:  # type: ignore[override]
        """Yield one GatewayItem per outing in the requested range."""
        window = await self.resolve_window()
        existing_by_day = await self._existing_by_day()

        async with httpx.AsyncClient(timeout=30) as http:
            if window.mode == "since_last_sync" and window.changed_since is not None:
                changed = await self._fetch_observations(
                    http, {"updated_since": window.changed_since.isoformat()}
                )
                days = {o["observed_on"] for o in changed if o.get("observed_on")}
                # An observation whose date was edited leaves its old day stale.
                changed_ids = {o.get("id") for o in changed}
                for day, outings in existing_by_day.items():
                    if any(ex.observation_ids & changed_ids for ex in outings):
                        days.add(day)
                by_day = await self._fetch_days(http, days)
            else:
                params: dict[str, Any] = {}
                if window.mode == "custom":
                    if window.start:
                        params["d1"] = window.start.isoformat()
                    if window.end:
                        params["d2"] = window.end.isoformat()
                everything = await self._fetch_observations(http, params)
                by_day = collections.defaultdict(list)
                for obs in everything:
                    if obs.get("observed_on"):
                        by_day[obs["observed_on"]].append(obs)

        for item in self._outings(by_day, existing_by_day):
            yield item

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _outings(
        self,
        by_day: dict[str, list[dict[str, Any]]],
        existing_by_day: dict[str, list[ExistingOuting]],
    ) -> Iterator[GatewayItem]:
        """The items for *by_day*'s observations, clustered at this gateway's radius."""
        for day in sorted(by_day):
            clusters = cluster_observations(by_day[day], self.cluster_radius_m)
            keys = assign_outing_keys(day, clusters, existing_by_day.get(day, []))
            for cluster, (ref, slug) in zip(clusters, keys, strict=True):
                yield build_outing_item(day, ref, slug, cluster)

    async def _fetch_days(
        self, http: httpx.AsyncClient, days: Iterable[str]
    ) -> dict[str, list[dict[str, Any]]]:
        """Every observation of each of *days*, by day."""
        return {
            day: await self._fetch_observations(http, {"observed_on": day}) for day in sorted(days)
        }

    async def _existing_by_day(self) -> dict[str, list[ExistingOuting]]:
        """Existing outing documents, grouped by the date they are for."""
        out: dict[str, list[ExistingOuting]] = collections.defaultdict(list)
        offset = 0
        while True:
            page = await self._client.list_documents(
                doc_type=self.block_type, limit=100, offset=offset
            )
            docs = page.get("documents", [])
            for doc in docs:
                if (parsed := existing_outing(doc)) is not None:
                    out[parsed[0]].append(parsed[1])
            offset += len(docs)
            if not docs or offset >= page.get("total", 0):
                return out

    async def _fetch_observations(
        self, http: httpx.AsyncClient, extra: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Page through the user's observations matching *extra*."""
        all_obs: list[dict[str, Any]] = []
        page = 1
        while True:
            resp = await http.get(
                f"{_INAT_API}/observations",
                params={
                    "user_login": self._username,
                    "per_page": _PAGE_SIZE,
                    "page": page,
                    "order_by": "observed_on",
                    "order": "asc",
                    **extra,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            if not results:
                break
            all_obs.extend(results)
            if len(all_obs) >= data.get("total_results", 0):
                break
            page += 1
        return all_obs
