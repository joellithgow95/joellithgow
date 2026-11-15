"""Shared fakes for the gateway and migration tests."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
from httpx import ASGITransport

INAT_URL = "https://api.inaturalist.org/v1/observations"


class RecordingTransport(ASGITransport):
    def __init__(self, app: Any) -> None:
        super().__init__(app=app)
        self.calls: list[tuple[str, str]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        return await super().handle_async_request(request)

    def writes(self) -> list[tuple[str, str]]:
        return [c for c in self.calls if c[0] != "GET"]


def raw_obs(
    oid: int,
    day: str,
    lat: float | None,
    lon: float | None,
    *,
    place: str = "Prospect Park, Brooklyn, NY",
    time: str = "10:00",
    common: str = "American Robin",
    name: str = "Turdus migratorius",
    iconic: str = "Aves",
    updated: str = "2000-01-01T00:00:00+00:00",
    **extra: Any,
) -> dict[str, Any]:
    """An observation shaped like iNaturalist's v1 API, noise included."""
    obs = {
        "id": oid,
        "uri": f"https://www.inaturalist.org/observations/{oid}",
        "observed_on": day,
        "time_observed_at": f"{day}T{time}:00-04:00",
        "place_guess": place,
        "quality_grade": "research",
        "taxon": {"name": name, "preferred_common_name": common, "iconic_taxon_name": iconic},
        "photos": [{"url": f"https://img.example/{oid}/square.jpg"}],
        "updated_at": updated,
        # The volatile bulk iNat returns and the site never shows:
        "comments": [],
        "faves": [],
        "comments_count": 0,
        "identifications": [{"id": oid, "body": "x" * 400}],
    }
    if lat is not None:
        obs["location"] = f"{lat},{lon}"
    obs.update(extra)
    return obs


class FakeINat:
    """Serves ``/v1/observations`` from a list, honouring the params the gateway uses."""

    def __init__(self) -> None:
        self.db: list[dict[str, Any]] = []
        self.requests: list[dict[str, str]] = []
        self.fail_on_observed_on: str | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        p = dict(request.url.params)
        self.requests.append(p)
        if self.fail_on_observed_on and p.get("observed_on") == self.fail_on_observed_on:
            return httpx.Response(500, text="boom")
        rows = list(self.db)
        if "observed_on" in p:
            rows = [o for o in rows if o["observed_on"] == p["observed_on"]]
        if "d1" in p:
            rows = [o for o in rows if o["observed_on"] >= p["d1"]]
        if "d2" in p:
            rows = [o for o in rows if o["observed_on"] <= p["d2"]]
        if "updated_since" in p:
            since = datetime.fromisoformat(p["updated_since"])
            rows = [o for o in rows if datetime.fromisoformat(o["updated_at"]) >= since]
        rows.sort(key=lambda o: (o["observed_on"], o["id"]))
        per, page = int(p.get("per_page", 30)), int(p.get("page", 1))
        return httpx.Response(
            200,
            json={"total_results": len(rows), "results": rows[(page - 1) * per : page * per]},
        )


# Two spots ~3 km apart, and a point 200 m from the first.
PARK = (40.6602, -73.9690)
PARK_NEAR = (40.6615, -73.9690)  # ~145 m
ZOO = (40.6670, -73.9650)  # ~860 m — close, still apart from the garden below
GARDEN = (40.6700, -73.9625)  # ~1.2 km from the park, ~340 m from the zoo
FAR = (40.7000, -73.9000)  # kilometres away


def liked(track_id: str, added_at: str, name: str = "Song", artist: str = "Artist") -> dict[str, Any]:
    return {
        "added_at": added_at,
        "track": {
            "id": track_id,
            "name": f"{name} {track_id}",
            "artists": [{"name": artist}],
            "album": {"name": "Album", "images": [{"url": "big"}, {"url": f"small-{track_id}"}]},
            "external_urls": {"spotify": f"https://open.spotify.com/track/{track_id}"},
            "popularity": 50,  # volatile; not stored
        },
    }


def saved_album(album_id: str, added_at: str, name: str = "Record", artist: str = "Band") -> dict[str, Any]:
    """A saved-album item shaped like Spotify's ``/me/albums``, noise included."""
    return {
        "added_at": added_at,
        "album": {
            "id": album_id,
            "name": f"{name} {album_id}",
            "artists": [{"name": artist}],
            "images": [{"url": f"big-{album_id}"}, {"url": "small"}],
            "release_date": "2026-05-01",
            "release_date_precision": "day",
            "total_tracks": 11,
            "external_urls": {"spotify": f"https://open.spotify.com/album/{album_id}"},
            "popularity": 50,  # volatile; not stored
            "tracks": {"items": [{"name": "x"}] * 11},  # bulky; not stored
        },
    }


class FakeSpotify:
    """``current_user_saved_tracks`` (and ``_albums``) over a newest-first list, 50 per page by default."""

    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items
        self.offsets: list[int] = []

    def current_user_saved_tracks(self, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        self.offsets.append(offset)
        page = self.items[offset : offset + limit]
        return {"items": page, "next": "more" if offset + limit < len(self.items) else None}

    current_user_saved_albums = current_user_saved_tracks


def library() -> list[dict[str, Any]]:
    """Newest first: Sep (2), Aug (2), Jul (1), and a pre-floor Dec 2024 track."""
    return [
        liked("s2", "2026-09-18T10:00:00Z"),
        liked("s1", "2026-09-02T10:00:00Z"),
        liked("a2", "2026-08-20T10:00:00Z"),
        liked("a1", "2026-08-01T10:00:00Z"),
        liked("j1", "2026-07-15T10:00:00Z"),
        liked("old", "2024-12-31T23:00:00Z"),
    ]
