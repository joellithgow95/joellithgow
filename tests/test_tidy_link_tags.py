"""
Tidying link tags against a real in-process CMS: renames, additions, drafts kept, and
everything written as drafts in one changeset.

Run with: uv run --with pytest --with pytest-asyncio --with respx python -m pytest tests/
"""

from __future__ import annotations

import argparse
import os
import tempfile

import httpx
import pytest
import pytest_asyncio
from starlette_cms import CMS

from cms import tidy_link_tags as tidy
from cms.schema import register_documents

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def http():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        cms = CMS(database_url=f"sqlite:///{db_path}", auth="none")
        register_documents(cms)
        async with cms.lifespan_context(None), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=cms.app), base_url="http://testserver"
        ) as client:
            yield client
    finally:
        os.unlink(db_path)


def args(**kw) -> argparse.Namespace:
    return argparse.Namespace(**{"cms_url": "http://testserver", "api_key": "k", "changeset": "Link tags", "apply": False, **kw})


async def link(http: httpx.AsyncClient, slug: str, tags: list[str]) -> str:
    resp = await http.post(
        "/api/documents",
        json={"doc_type": "link", "slug": slug, "body": {"title": slug, "url": f"https://{slug}.example", "date_added": "2026-10-01", "tags": tags}},
    )
    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["id"]
    assert (await http.post(f"/api/documents/{doc_id}/publish")).status_code == 200
    return doc_id


async def draft_tags(http: httpx.AsyncClient, doc_id: str) -> list[str]:
    return (await http.get(f"/api/documents/{doc_id}", params={"draft": "true"})).json()["body"]["tags"]


async def test_tidy_tags_renames_drops_adds_and_dedupes():
    assert tidy.tidy_tags(["friends", "design-portfolio", "design", "visual-design"]) == ["friends", "design"]
    assert tidy.tidy_tags(["developer-blogs", "development", "tech-blog", "ai"]) == ["developer-blogs", "ai"]
    assert tidy.tidy_tags(["ai"], ["developer-blogs"]) == ["ai", "developer-blogs"]


async def test_tidy_slices_turns_one_tag_into_a_list_and_leaves_the_rest():
    data = [{"block_type": "links_by_tag", "tag": "inspo"}, {"block_type": "link_picks", "refs": ["a"]}]
    assert tidy.tidy_slices(data) == [{"block_type": "links_by_tag", "tags": ["inspo"]}, data[1]]


async def test_report_lists_the_changes_and_writes_nothing(http, capsys):
    rebecca = await link(http, "rebecca-powells-blog", ["ai"])
    await link(http, "tidy-already", ["inspo"])

    assert await tidy.run(args(), http=http) == 0

    out = capsys.readouterr().out
    assert "rebecca-powells-blog" in out and "ai, developer-blogs" in out
    assert "tidy-already" not in out
    assert await draft_tags(http, rebecca) == ["ai"]


async def test_apply_writes_drafts_into_one_changeset_and_a_rerun_finds_nothing(http, capsys):
    rebecca = await link(http, "rebecca-powells-blog", ["ai"])
    marcards = await link(http, "marcards", ["friends", "design-portfolio", "design"])

    assert await tidy.run(args(apply=True), http=http) == 0

    assert await draft_tags(http, rebecca) == ["ai", "developer-blogs"]
    assert await draft_tags(http, marcards) == ["friends", "design"]
    live = (await http.get(f"/api/documents/{rebecca}")).json()["body"]["tags"]
    assert live == ["ai"]  # nothing published
    sets = (await http.get("/api/changesets", params={"include_documents": "true"})).json()["changesets"]
    held = {cs["title"]: {d["id"] for d in cs["documents"]} for cs in sets}
    assert {rebecca, marcards} <= held["Link tags"]

    capsys.readouterr()
    assert await tidy.run(args(apply=True), http=http) == 0
    assert "Nothing to do" in capsys.readouterr().out


async def test_a_pending_draft_edit_is_kept(http):
    doc = await link(http, "rebecca-powells-blog", ["ai"])
    await http.patch(f"/api/documents/{doc}", json={"body": {"description": "half written"}})

    await tidy.run(args(apply=True), http=http)

    body = (await http.get(f"/api/documents/{doc}", params={"draft": "true"})).json()["body"]
    assert body["description"] == "half written"
    assert body["tags"] == ["ai", "developer-blogs"]
