"""
Composing the monthly music post, against a real in-process CMS with the site's schema.

Run with: uv run --with pytest --with pytest-asyncio --with respx python -m pytest tests/
"""

from __future__ import annotations

import argparse
import os
import tempfile

import httpx
import pytest
import pytest_asyncio
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette_cms import CMS

from cms import compose_music_month as compose
from cms.schema import register_documents


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def http():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        cms = CMS(database_url=f"sqlite:///{db_path}", auth="none", default_changeset="Staging")
        register_documents(cms)
        app = Starlette(routes=[Mount("/", app=cms.app)])
        async with cms.lifespan_context(None):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                yield client
    finally:
        os.unlink(db_path)


async def live(http: httpx.AsyncClient, doc_type: str, slug: str, body: dict, ref: str) -> str:
    resp = await http.post(
        "/api/documents", json={"doc_type": doc_type, "slug": slug, "import_ref": ref, "body": body}
    )
    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["id"]
    assert (await http.post(f"/api/documents/{doc_id}/publish")).status_code == 200
    return doc_id


async def like(http: httpx.AsyncClient, track: str, liked_at: str) -> str:
    return await live(
        http, "spotify_like", f"spotify-like-{track}",
        {"title": track, "track_id": track, "track_name": track, "liked_at": liked_at},
        f"spotify:like:{track}",
    )


async def posts(http: httpx.AsyncClient) -> dict[str, dict]:
    return {p["slug"]: p for p in await compose.documents(http, "blog_post")}


def compose_args(**kw) -> argparse.Namespace:
    base = {"cms_url": "http://testserver", "api_key": "k", "month": "2026-09", "apply": True}
    return argparse.Namespace(**{**base, **kw})


async def test_compose_makes_one_draft_in_staging_and_never_twice(http):
    await like(http, "a", "2026-09-03T10:00:00Z")

    assert await compose.run(compose_args(), http=http) == 0
    assert await compose.run(compose_args(), http=http) == 0

    [post] = (await posts(http)).values()
    assert post["slug"] == "spotify-dump-2026-09"
    assert post["published"] is False
    assert post["body"]["data"] == [{"block_type": "music_range", "from_date": "2026-09-01", "to_date": "2026-09-30"}]
    staging = (await http.get("/api/changesets/default")).json()["changeset"]
    held = (await http.get(f"/api/changesets/{staging['id']}")).json()["documents"]
    assert [d["id"] for d in held] == [post["id"]]


async def test_compose_skips_a_month_that_already_has_a_post(http):
    await like(http, "a", "2026-09-03T10:00:00Z")
    await live(http, "blog_post", "spotify-dump-2026-09", compose.music_month_post("2026-09")["body"], "music:month:2026-09")

    assert await compose.run(compose_args(), http=http) == 0

    assert len(await posts(http)) == 1


async def test_compose_makes_nothing_for_a_month_with_no_likes(http):
    assert await compose.run(compose_args(month="2026-08"), http=http) == 0
    assert await posts(http) == {}


def test_previous_month_and_ranges():
    from datetime import date

    assert compose.previous_month(date(2026, 12, 1)) == "2026-11"
    assert compose.previous_month(date(2027, 1, 1)) == "2026-12"
    assert compose.month_range("2028-02") == ("2028-02-01", "2028-02-29")
