"""
Tests for cms/media.py: astraeus-media mounted beside a real in-process CMS.

Nothing here reaches R2. Signing a presigned URL is local, and the routes under
test never read or write an object.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette_cms import CMS
from starlette_cms.session import generate_session_token

from cms.media import MOUNT_PATH, build_media
from cms.schema import register_documents

SESSION_SECRET = "session-secret"
API_KEY = "media-key"
ENDPOINT = "https://acct.r2.example.com"
KEY = "originals/ab12cd34/photo.png"


def media_env(tmp_path, **extra) -> dict[str, str]:
    return {
        "MEDIA_BUCKET": "jlithgow-media",
        "MEDIA_ENDPOINT_URL": ENDPOINT,
        "MEDIA_ACCESS_KEY_ID": "id",
        "MEDIA_SECRET_ACCESS_KEY": "secret",
        "MEDIA_CATALOG_PATH": str(tmp_path / "media.db"),
        "MEDIA_API_KEY": API_KEY,
        **extra,
    }


@pytest_asyncio.fixture
async def site(tmp_path):
    """``(client, mk)``: the CMS at "/" with astraeus-media above it, as in cms/main.py."""
    cms = CMS(
        database_url=f"sqlite:///{tmp_path / 'content.db'}",
        auth="apikey",
        api_key="cms-key",
        read_auth=False,
        session_secret=SESSION_SECRET,
    )
    register_documents(cms)
    mk = build_media(cms, media_env(tmp_path))
    assert mk is not None
    app = Starlette(routes=[Mount(MOUNT_PATH, app=mk.app), Mount("/", app=cms.app)])
    async with cms.lifespan_context(app), mk.lifespan_context(app):
        await mk.catalog.insert_asset(
            key=KEY,
            content_hash="h",
            bucket="jlithgow-media",
            filename="photo.png",
            content_type="image/webp",
            size=10,
            width=40,
            height=30,
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            yield client, mk


def session_cookie() -> dict[str, str]:
    return {"cms_session": generate_session_token("joel", secret=SESSION_SECRET)}


def test_no_bucket_means_no_media(tmp_path):
    cms = CMS(database_url=f"sqlite:///{tmp_path / 'c.db'}", auth="none")
    assert build_media(cms, {}) is None


@pytest.mark.asyncio
async def test_media_routes_beat_the_cms_catch_all(site):
    client, _ = site
    res = await client.get("/media/assets")
    assert res.status_code == 200
    assert [a["key"] for a in res.json()["assets"]] == [KEY]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/media/upload/prepare"),
        ("POST", "/media/upload/confirm"),
        ("PATCH", f"/media/assets/{KEY}"),
        ("DELETE", f"/media/assets/{KEY}"),
        ("POST", "/media/references"),
        ("DELETE", "/media/references"),
    ],
)
async def test_everything_that_writes_is_401_anonymous(site, method, path):
    client, _ = site
    res = await client.request(method, path, json={})
    assert res.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ["/media/admin", "/media/admin/upload", f"/media/admin/assets/{KEY}"]
)
async def test_the_admin_pages_send_a_signed_out_visitor_to_the_login(site, path):
    """Browsing the library needs the login; the page sends you there and back."""
    client, _ = site
    res = await client.get(path)
    assert res.status_code == 302
    assert res.headers["location"].startswith("/api/auth/login?next=%2Fmedia%2Fadmin")
    assert "photo" not in res.text


@pytest.mark.asyncio
async def test_reads_the_site_needs_are_public(site):
    client, _ = site
    assert (await client.get("/media/assets")).status_code == 200
    # The image route is what <img src> hits. 404 would mean routing, not auth.
    res = await client.get(f"/media/iiif/{KEY}/info.json")
    assert res.status_code == 200


@pytest.mark.asyncio
async def test_session_cookie_gets_the_admin_with_prefixed_links(site):
    client, _ = site
    res = await client.get("/media/admin?picker=1", cookies=session_cookie())
    assert res.status_code == 200
    assert 'data-media-base="/media"' in res.text
    assert f"/media/iiif/{KEY}/square/256,/0/default.webp" in res.text


@pytest.mark.asyncio
async def test_a_forged_session_cookie_is_refused(site):
    client, _ = site
    bad = {"cms_session": generate_session_token("joel", secret="not-the-secret")}
    res = await client.get("/media/admin", cookies=bad)
    assert res.status_code == 302
    assert res.headers["location"].startswith("/api/auth/login")


@pytest.mark.asyncio
async def test_bearer_key_works_and_a_wrong_one_does_not(site):
    client, _ = site
    ok = await client.patch(
        f"/media/assets/{KEY}",
        json={"alt_text": "A trail"},
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    assert ok.status_code == 200
    assert ok.json()["alt_text"] == "A trail"
    bad = await client.patch(
        f"/media/assets/{KEY}",
        json={"alt_text": "x"},
        headers={"Authorization": "Bearer nope"},
    )
    assert bad.status_code == 401


@pytest.mark.asyncio
async def test_without_a_media_api_key_the_bearer_path_is_closed(tmp_path):
    cms = CMS(
        database_url=f"sqlite:///{tmp_path / 'c.db'}", auth="none", session_secret=SESSION_SECRET
    )
    env = media_env(tmp_path)
    del env["MEDIA_API_KEY"]
    mk = build_media(cms, env)
    assert mk is not None
    app = Starlette(routes=[Mount(MOUNT_PATH, app=mk.app)])
    async with mk.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            # An empty bearer must not match an unset key.
            res = await client.post(
                "/media/upload/prepare", json={}, headers={"Authorization": "Bearer "}
            )
            assert res.status_code == 401


@pytest.mark.asyncio
async def test_prepare_upload_signs_against_the_r2_bucket(site):
    client, _ = site
    res = await client.post(
        "/media/upload/prepare",
        json={"filename": "photo.png", "content_type": "image/png", "size": 1234},
        cookies=session_cookie(),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["upload_url"].startswith(ENDPOINT)
    assert "jlithgow-media" in body["upload_url"]
    assert "X-Amz-Signature" in body["upload_url"]
    assert body["key"].startswith("originals/")


def test_renditions_bucket_and_public_url_come_from_the_environment(tmp_path):
    cms = CMS(database_url=f"sqlite:///{tmp_path / 'content.db'}", session_secret=SESSION_SECRET)
    env = media_env(
        tmp_path,
        MEDIA_DERIVATIVES_BUCKET="jlithgow-media-public",
        MEDIA_DERIVATIVES_PUBLIC_URL="https://media.joellithgow.com",
    )

    mk = build_media(cms, env)

    assert mk is not None
    assert mk.config.derivatives_bucket == "jlithgow-media-public"
    assert mk.config.derivatives_public_url == "https://media.joellithgow.com"


def test_renditions_stay_beside_the_originals_without_a_bucket_setting(tmp_path):
    cms = CMS(database_url=f"sqlite:///{tmp_path / 'content.db'}", session_secret=SESSION_SECRET)

    mk = build_media(cms, media_env(tmp_path))

    assert mk is not None
    assert mk.config.derivatives_bucket is None
