"""
Spotify Liked Songs gateway — one CMS document per calendar month.

Legacy (ADR 026 in astraeus): ``spotify_likes`` writes one data document per song
instead. This gateway keeps the month posts current until the migration turns them
into posts that slice those likes, and is removed then.

Liked tracks come back newest first, so a run only pages back as far as the
earliest month it needs to refresh, groups tracks by YYYY-MM and yields one
GatewayItem per month. Re-syncing mid-month updates that month's document with
any newly liked songs and publishes it.

Ownership. ``songs`` and ``song_count`` are machine-sourced and refreshed on
every sync that touches the month. The title, publish date and tags are seeded
when the month is first created and then belong to whoever edits them.

Ranges (``self.range``, see ``BaseGateway.sync``):
    since_last_sync  refresh every month from the one containing the cursor
                     (minus the gateway's overlap) up to now.
    all_time         every month from ``MONTH_FLOOR``.
    custom           the months containing ``start``..``end``; the floor does
                     not apply, so an explicit backfill can reach earlier.

A track you un-like disappears from its month only when that month is
refreshed again. A month before the floor is only ever touched by ``custom``.
A document is never deleted by a sync.

A month a run leaves alone (a person's draft is on it) or fails on is reported in
the sync reply and not remembered: an incremental run only meets it again if you
like or un-like a song in it, so catch it up with an ``all_time`` run.

Environment variables:
    SPOTIPY_CLIENT_ID       Spotify application client ID
    SPOTIPY_CLIENT_SECRET   Spotify application client secret
    SPOTIPY_REFRESH_TOKEN   Long-lived refresh token (preferred; obtain once
                            via ``python cms/gateways/get_spotify_token.py``)

    Legacy OAuth dance (only needed to obtain the initial refresh token):
    SPOTIPY_REDIRECT_URI    OAuth redirect URI (e.g. http://localhost:8888/callback)
"""

from __future__ import annotations

import asyncio
import calendar
import collections
import os
from collections.abc import AsyncIterator, Iterator
from typing import Any

import spotipy
from spotipy.oauth2 import SpotifyOAuth

from starlette_cms_gateways import BaseGateway, GatewayItem, SyncWindow

_SCOPE = "user-library-read"

# Only sync months from this point forward (inclusive, "YYYY-MM" lexical compare).
# The library goes back to 2017, and the site only wants 2025 on. Months before the
# floor were synced once, before it existed: those 31 posts stay in the CMS until
# the gateway migration (cms/migrate_gateway_docs.py) deletes them. Nothing but
# that migration removes a document, so a *custom* range, which ignores the floor
# on purpose, re-creates any pre-floor month it covers as a new published post.
MONTH_FLOOR = "2025-01"
_PAGE_SIZE = 50


def month_bounds(window: SyncWindow) -> tuple[str | None, str | None]:
    """
    ``(first_month, last_month)`` as ``"YYYY-MM"`` to refresh for *window*;
    ``None`` is open-ended on that side.
    """
    if window.mode == "custom":
        first = window.start.strftime("%Y-%m") if window.start else None
        last = window.end.strftime("%Y-%m") if window.end else None
        return first, last
    if window.mode == "since_last_sync" and window.changed_since is not None:
        return max(MONTH_FLOOR, window.changed_since.strftime("%Y-%m")), None
    return MONTH_FLOOR, None


def curate_track(item: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """``(YYYY-MM, song)`` for one saved-track item — only what the site shows."""
    track = item.get("track") or {}
    liked_at: str = item.get("added_at", "")
    album = track.get("album") or {}
    album_images = album.get("images") or []
    return liked_at[:7], {
        "track_name": track.get("name", ""),
        "artist_name": ", ".join(a.get("name", "") for a in track.get("artists", [])),
        "album_name": album.get("name", ""),
        # Smallest image for the thumbnail (last in the list)
        "album_art_url": album_images[-1]["url"] if album_images else "",
        "spotify_url": (track.get("external_urls") or {}).get("spotify", ""),
        "liked_at": liked_at,
    }


def spotify_client_from_env() -> spotipy.Spotify:
    """A Spotify client authorised from the ``SPOTIPY_*`` environment variables."""
    client_id = os.environ["SPOTIPY_CLIENT_ID"]
    client_secret = os.environ["SPOTIPY_CLIENT_SECRET"]
    refresh_token = os.environ.get("SPOTIPY_REFRESH_TOKEN")

    if refresh_token:
        # Use a pre-obtained refresh token — no browser / redirect needed.
        auth_manager = SpotifyOAuth(
            scope=_SCOPE,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri="http://localhost:8888/callback",  # not called
        )
        token_info = auth_manager.refresh_access_token(refresh_token)
        return spotipy.Spotify(auth=token_info["access_token"])
    # Fall back to interactive OAuth dance (requires SPOTIPY_REDIRECT_URI).
    return spotipy.Spotify(
        auth_manager=SpotifyOAuth(
            scope=_SCOPE,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=os.environ["SPOTIPY_REDIRECT_URI"],
        )
    )


class SpotifyLikedDumpGateway(BaseGateway):
    """Legacy: one post per month. Still keeps the live month posts current, so run it
    alongside spotify-likes (one data document per song, ADR 026) until the migration
    converts those posts and removes it."""

    service_name = "spotify_liked_dump"
    block_type = "spotify_liked_dump"
    auto_publish = True
    default_range = "since_last_sync"
    owned_fields = ("songs", "song_count")

    def __init__(self, *, spotify_client: spotipy.Spotify | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._sp = spotify_client if spotify_client is not None else spotify_client_from_env()

    async def fetch(self) -> AsyncIterator[GatewayItem]:  # type: ignore[override]
        """Yield one GatewayItem per YYYY-MM bucket of liked tracks in range."""
        window = await self.resolve_window()
        first_month, last_month = month_bounds(window)
        for item in self._month_items(await self._collect(first_month, last_month)):
            yield item

    async def _collect(
        self, first_month: str | None, last_month: str | None
    ) -> dict[str, list[dict[str, Any]]]:
        """Liked songs by ``YYYY-MM`` from *first_month* to *last_month* (``None``: open)."""
        tracks_by_month: dict[str, list[dict]] = collections.defaultdict(list)
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
                month_key, song = curate_track(item)
                if not month_key:
                    continue
                if first_month is not None and month_key < first_month:
                    # Newest first: everything from here on is older than we need,
                    # and every track of first_month has already been seen.
                    reached_before_range = True
                    break
                if last_month is None or month_key <= last_month:
                    tracks_by_month[month_key].append(song)

            if reached_before_range or result.get("next") is None:
                break
            offset += _PAGE_SIZE
        return tracks_by_month

    @staticmethod
    def _month_items(tracks_by_month: dict[str, list[dict[str, Any]]]) -> Iterator[GatewayItem]:
        """One GatewayItem per month, oldest first."""
        for month_key in sorted(tracks_by_month):
            songs = tracks_by_month[month_key]
            year_str, month_str = month_key.split("-")
            month_label = calendar.month_name[int(month_str)]
            title = f"What I've been listening to — {month_label} {year_str}"

            yield GatewayItem(
                import_ref=f"spotify:dump:{month_key}",
                slug=f"spotify-dump-{month_key}",
                title=title,
                body={
                    # Seeded once, then the editor's:
                    "title": title,
                    "publish_date": f"{month_key}-01",
                    "tags": ["music"],
                    # Owned by the gateway (see SpotifyLikedDumpGateway.owned_fields):
                    "song_count": float(len(songs)),
                    "songs": songs,
                },
            )
