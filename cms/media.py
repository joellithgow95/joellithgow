"""
Media library: astraeus-media mounted at /media, backed by a private R2 bucket.

astraeus-media keeps the originals in R2 and serves every image itself
(``/media/iiif/<key>/...`` answers with a 302 to a short-lived presigned URL), so
the bucket stays private and its credentials never leave the pod.

With ``MEDIA_DERIVATIVES_BUCKET`` set, renditions live in a separate public bucket and
``/media/iiif/...`` redirects to ``MEDIA_DERIVATIVES_PUBLIC_URL``, so Cloudflare's edge
serves them; originals are never in that bucket.

Read routes are public on purpose: the static site's <img> tags carry no
credentials. Everything that writes, and the admin pages, take ``media_auth``:
the CMS login cookie (the browser, and the editor's picker) or
``Authorization: Bearer $MEDIA_API_KEY`` (the media MCP, if there is one).

Only built when ``MEDIA_BUCKET`` is set, so local dev and staging run without it.
"""

from __future__ import annotations

import hmac
import os
from collections.abc import Callable, Mapping

from astraeus_media import MediaKit, MediakitConfig
from starlette.requests import Request
from starlette_cms import CMS
from starlette_cms.auth import check_session_auth

MOUNT_PATH = "/media"


def media_auth(cms: CMS, api_key: str | None) -> Callable[[Request], bool]:
    """One callable for both ways in; astraeus-media takes a single auth mode."""

    def check(request: Request) -> bool:
        if check_session_auth(request, cms):
            return True
        header = request.headers.get("Authorization", "")
        if api_key and header.startswith("Bearer "):
            return hmac.compare_digest(header[len("Bearer ") :], api_key)
        return False

    return check


def build_media(
    cms: CMS, env: Mapping[str, str] = os.environ, *, portal_url: str | None = None
) -> MediaKit | None:
    """Return a configured MediaKit, or ``None`` when ``MEDIA_BUCKET`` is unset."""
    bucket = env.get("MEDIA_BUCKET")
    if not bucket:
        return None

    return MediaKit(
        config=MediakitConfig(
            bucket=bucket,
            endpoint_url=env.get("MEDIA_ENDPOINT_URL"),
            aws_access_key_id=env.get("MEDIA_ACCESS_KEY_ID"),
            aws_secret_access_key=env.get("MEDIA_SECRET_ACCESS_KEY"),
            # Renditions in a public bucket of their own, served from a custom
            # domain: originals stay private. Unset, they sit beside the originals.
            derivatives_bucket=env.get("MEDIA_DERIVATIVES_BUCKET"),
            derivatives_public_url=env.get("MEDIA_DERIVATIVES_PUBLIC_URL"),
            # A file of its own beside content.db: Litestream replicates it
            # separately, and a catalog problem can't touch the documents.
            catalog_path=env.get("MEDIA_CATALOG_PATH", "./cms/data/media.db"),
            public_read=False,
            auth=media_auth(cms, env.get("MEDIA_API_KEY")),
            # The CMS is mounted at "/", so its static files are not under /cms.
            tokens_url="/static/tokens.css",
            mount_path=MOUNT_PATH,
            # Signed out, the admin pages send you to the CMS login and back.
            login_url="/api/auth/login",
            # The portal's app bar across the top: home, and the other apps.
            portal_url=portal_url,
        )
    )
