"""
Spotify albums gateway — one ``spotify_album`` data document per saved album.

The album twin of ``spotify_likes``: same Spotify app, same refresh token and
the same ``user-library-read`` scope (it covers both the saved-tracks and the
saved-albums list), same ranges and floor. ADR 026 (astraeus): one item per
document, no grouping; a post shows an album through an ``album_picks`` slice,
and a post with no body that picks one album is an album beat on the feed.

Every body field is the gateway's (``owned_fields`` lists them all), so a re-sync
that sees a change overwrites the record. An album you remove from your library
keeps its document: no sync deletes one.

Ranges (``self.range``, see ``BaseGateway.sync``), by the date the album was saved:
    since_last_sync  albums saved since the cursor (minus the gateway's overlap).
    all_time         every album saved on or after ``LIKED_FLOOR``.
    custom           albums saved between the given dates (inclusive), floor or not.

Environment variables: the ``SPOTIPY_*`` ones ``spotify_likes`` reads.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import spotipy
from starlette_cms_gateways import BaseGateway, GatewayItem

from cms.gateways.spotify_likes import (
    collect_saved,
    liked_bounds,
    spotify_client_from_env,
)

# Every field of a spotify_album body; all of them belong to the gateway.
ALBUM_FIELDS = (
    "title",
    "album_id",
    "album_name",
    "artist_name",
    "release_date",
    "total_tracks",
    "cover_url",
    "spotify_url",
    "saved_at",
)


def curate_album(item: dict[str, Any]) -> dict[str, Any] | None:
    """The ``spotify_album`` body for one saved-album item, or ``None`` when it
    has no Spotify id or no save time."""
    album = item.get("album") or {}
    album_id = album.get("id")
    saved_at: str = item.get("added_at") or ""
    if not album_id or not saved_at:
        return None
    images = album.get("images") or []
    album_name = album.get("name") or ""
    artist_name = ", ".join(a.get("name", "") for a in album.get("artists") or [])
    return {
        # A label for the editor's list and pickers, made from the record.
        "title": f"{album_name} — {artist_name}" if artist_name else album_name,
        "album_id": album_id,
        "album_name": album_name,
        "artist_name": artist_name,
        # "2024-03-01", "2024-03" or "2024", as precise as Spotify knows it.
        "release_date": album.get("release_date") or "",
        "total_tracks": album.get("total_tracks"),
        # The largest cover (first in the list, 640px): a beat shows it big.
        "cover_url": images[0]["url"] if images else "",
        "spotify_url": (album.get("external_urls") or {}).get("spotify", ""),
        "saved_at": saved_at,
    }


def album_item(body: dict[str, Any]) -> GatewayItem:
    album_id = body["album_id"]
    return GatewayItem(
        import_ref=f"spotify:album:{album_id}",
        slug=f"spotify-album-{album_id}",
        title=body["title"],
        body=body,
    )


class SpotifyAlbumsGateway(BaseGateway):
    """Sync Spotify saved albums into the CMS, one data document per album."""

    service_name = "spotify_albums"
    block_type = "spotify_album"
    auto_publish = True
    default_range = "since_last_sync"
    owned_fields = ALBUM_FIELDS

    def __init__(self, *, spotify_client: spotipy.Spotify | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._sp = spotify_client if spotify_client is not None else spotify_client_from_env()

    async def fetch(self) -> AsyncIterator[GatewayItem]:  # type: ignore[override]
        """Yield one GatewayItem per album saved within the window, oldest first."""
        window = await self.resolve_window()
        earliest, last_day = liked_bounds(window)
        albums = await collect_saved(
            self._sp.current_user_saved_albums, curate_album, earliest, last_day
        )
        for body in reversed(albums):
            yield album_item(body)
