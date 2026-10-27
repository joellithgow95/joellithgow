"""
Spotify likes gateway — one ``spotify_like`` data document per liked song.

ADR 026 (astraeus): collected data is stored one item per document, with no
grouping of its own. A post picks the songs it is about (a month, an album, three
tracks) through a slice; the gateway only keeps the records current. This
replaces the month-shaped ``spotify_liked_dump`` gateway, which stays registered
until the migration converts its posts.

Every body field is the gateway's (``owned_fields`` lists them all), so a re-sync
that sees a change overwrites the whole record, and a draft on a like is never
treated as a person's work: a data document is not for hand edits.

Ranges (``self.range``, see ``BaseGateway.sync``):
    since_last_sync  songs liked since the cursor (minus the gateway's overlap).
    all_time         every song liked on or after ``LIKED_FLOOR``.
    custom           songs liked between the given dates (inclusive); the floor
                     does not apply, so an explicit backfill can reach earlier.

Liked tracks come back newest first, so a run stops paging at the first track
older than its window. A song you un-like keeps its document: no sync deletes
one, and the saved-tracks list says nothing about removals.

Environment variables: the same as ``spotify_liked_dump`` (``SPOTIPY_CLIENT_ID``,
``SPOTIPY_CLIENT_SECRET``, ``SPOTIPY_REFRESH_TOKEN``).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Any

import spotipy
from starlette_cms_gateways import BaseGateway, GatewayItem, SyncWindow

from .spotify_liked_dump import MONTH_FLOOR, spotify_client_from_env

# The site only wants likes from 2025 on (the same floor as the monthly gateway).
LIKED_FLOOR = date.fromisoformat(f"{MONTH_FLOOR}-01")
_PAGE_SIZE = 50

# Every field of a spotify_like body; all of them belong to the gateway.
LIKE_FIELDS = (
    "title",
    "track_id",
    "track_name",
    "artist_name",
    "album_name",
    "album_art_url",
    "spotify_url",
    "liked_at",
)


def _parse_time(raw: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def liked_bounds(window: SyncWindow) -> tuple[datetime | None, date | None]:
    """
    ``(earliest liked_at, last liked date)`` to fetch for *window*; ``None`` is
    open-ended on that side.
    """
    floor = datetime.combine(LIKED_FLOOR, datetime.min.time(), tzinfo=UTC)
    if window.mode == "custom":
        start = (
            datetime.combine(window.start, datetime.min.time(), tzinfo=UTC)
            if window.start
            else None
        )
        return start, window.end
    if window.mode == "since_last_sync" and window.changed_since is not None:
        return max(floor, window.changed_since), None
    return floor, None


def curate_like(item: dict[str, Any]) -> dict[str, Any] | None:
    """The ``spotify_like`` body for one saved-track item, or ``None`` when it has
    no Spotify id (a local file) or no like time."""
    track = item.get("track") or {}
    track_id = track.get("id")
    liked_at: str = item.get("added_at") or ""
    if not track_id or not liked_at:
        return None
    album = track.get("album") or {}
    album_images = album.get("images") or []
    track_name = track.get("name") or ""
    artist_name = ", ".join(a.get("name", "") for a in track.get("artists") or [])
    return {
        # A label for the editor's list and pickers, made from the record.
        "title": f"{track_name} — {artist_name}" if artist_name else track_name,
        "track_id": track_id,
        "track_name": track_name,
        "artist_name": artist_name,
        "album_name": album.get("name") or "",
        # Smallest image for the thumbnail (last in the list)
        "album_art_url": album_images[-1]["url"] if album_images else "",
        "spotify_url": (track.get("external_urls") or {}).get("spotify", ""),
        "liked_at": liked_at,
    }


def like_item(body: dict[str, Any]) -> GatewayItem:
    track_id = body["track_id"]
    return GatewayItem(
        import_ref=f"spotify:like:{track_id}",
        slug=f"spotify-like-{track_id}",
        title=body["title"],
        body=body,
    )


class SpotifyLikesGateway(BaseGateway):
    """Sync Spotify liked songs into the CMS, one data document per song."""

    service_name = "spotify_likes"
    block_type = "spotify_like"
    auto_publish = True
    default_range = "since_last_sync"
    owned_fields = LIKE_FIELDS

    def __init__(self, *, spotify_client: spotipy.Spotify | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._sp = spotify_client if spotify_client is not None else spotify_client_from_env()

    async def fetch(self) -> AsyncIterator[GatewayItem]:  # type: ignore[override]
        """Yield one GatewayItem per song liked within the window, oldest first."""
        window = await self.resolve_window()
        earliest, last_day = liked_bounds(window)
        likes = await self._collect(earliest, last_day)
        for body in reversed(likes):
            yield like_item(body)

    async def _collect(
        self, earliest: datetime | None, last_day: date | None
    ) -> list[dict[str, Any]]:
        """Liked songs from *earliest* to the end of *last_day*, newest first."""
        likes: list[dict[str, Any]] = []
        offset = 0
        while True:
            # spotipy is synchronous; keep the event loop free while it waits.
            result = await asyncio.to_thread(
                self._sp.current_user_saved_tracks, limit=_PAGE_SIZE, offset=offset
            )
            items = result.get("items", [])
            if not items:
                break

            reached_before_range = False
            for item in items:
                liked_at = _parse_time(item.get("added_at") or "")
                if liked_at is None:
                    continue
                if earliest is not None and liked_at < earliest:
                    # Newest first: everything from here on is older still.
                    reached_before_range = True
                    break
                if last_day is not None and liked_at.date() > last_day:
                    continue
                if (body := curate_like(item)) is not None:
                    likes.append(body)

            if reached_before_range or result.get("next") is None:
                break
            offset += _PAGE_SIZE
        return likes
