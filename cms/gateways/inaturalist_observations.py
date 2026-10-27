"""
iNaturalist observations gateway — one ``inat_observation`` data document per sighting.

ADR 026 (astraeus): collected data is stored one item per document, with no
grouping of its own. Which sightings make an outing, a trip or a post about birds
is a post's choice (a slice), not the gateway's, so the 3 km clustering of the
``inaturalist_field_trips`` gateway is not used here. That gateway stays registered
until the migration converts its outing posts.

The record is the same slim one the outings store (``curate_observation``), plus
the photo URLs and the taxon-group tag (``birds``, ``plants``…). No quality grade:
community IDs change it, which would republish a sighting for nothing the site
shows. Every body field is the gateway's (``owned_fields`` lists them all), so a
re-sync that sees a change overwrites the whole record, and a draft on a sighting
is never treated as a person's work.

Ranges (``self.range``, see ``BaseGateway.sync``):
    since_last_sync  observations iNaturalist changed since the cursor
                     (``updated_since``), whatever date they were observed on.
    all_time         every observation.
    custom           observations *observed* between the given dates.

An observation deleted at iNaturalist keeps its document: no sync deletes one.
One with no observed date is skipped, since a slice could never find it.

Environment variables:
    INATURALIST_USERNAME   iNaturalist username to fetch observations for
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import httpx
from starlette_cms_gateways import BaseGateway, GatewayItem

from .inaturalist_field_trips import curate_observation, photo_urls, taxon_tags

_INAT_API = "https://api.inaturalist.org/v1"
_PAGE_SIZE = 200

# Every field of an inat_observation body; all of them belong to the gateway.
OBSERVATION_FIELDS = (
    "title",
    "observation_id",
    "uri",
    "observed_on",
    "time_observed_at",
    "taxon_name",
    "common_name",
    "iconic_taxon",
    "place_guess",
    "lat",
    "lon",
    "photo_urls",
    "tags",
)


def observation_body(obs: dict[str, Any]) -> dict[str, Any] | None:
    """The ``inat_observation`` body for one raw observation, or ``None`` when it
    has no id or no observed date."""
    slim = curate_observation(obs)
    oid = slim.pop("id")
    if oid is None or not slim["observed_on"]:
        return None
    species = slim["common_name"] or slim["taxon_name"] or "Unknown species"
    place = slim["place_guess"]
    return {
        # A label for the editor's list and pickers, made from the record.
        "title": f"{species} — {place}" if place else species,
        "observation_id": float(oid),
        **slim,
        # Explicit, so a sighting that loses its position (obscured later) loses it here too.
        "lat": slim.get("lat"),
        "lon": slim.get("lon"),
        "photo_urls": photo_urls([obs]),
        "tags": taxon_tags([obs]),
    }


def observation_item(body: dict[str, Any]) -> GatewayItem:
    oid = int(body["observation_id"])
    return GatewayItem(
        import_ref=f"inaturalist:observation:{oid}",
        slug=f"inat-observation-{oid}",
        title=body["title"],
        body=body,
    )


class INaturalistObservationsGateway(BaseGateway):
    """Sync iNaturalist observations into the CMS, one data document per sighting."""

    service_name = "inaturalist_observations"
    block_type = "inat_observation"
    auto_publish = True
    default_range = "since_last_sync"
    owned_fields = OBSERVATION_FIELDS

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._username = os.environ["INATURALIST_USERNAME"]

    async def fetch(self) -> AsyncIterator[GatewayItem]:  # type: ignore[override]
        """Yield one GatewayItem per observation in the window, by observed date."""
        window = await self.resolve_window()
        params: dict[str, Any] = {}
        if window.mode == "since_last_sync" and window.changed_since is not None:
            params["updated_since"] = window.changed_since.isoformat()
        elif window.mode == "custom":
            if window.start:
                params["d1"] = window.start.isoformat()
            if window.end:
                params["d2"] = window.end.isoformat()

        async with httpx.AsyncClient(timeout=30) as http:
            observations = await self._fetch_observations(http, params)
        for obs in observations:
            if (body := observation_body(obs)) is not None:
                yield observation_item(body)

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
