"""Slices on blog_post (ADR 026): what the CMS accepts and refuses on save."""

from __future__ import annotations

import os
import tempfile

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from starlette_cms import CMS

from cms.schema import register_documents

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        cms = CMS(database_url=f"sqlite:///{db_path}", auth="none")
        register_documents(cms)
        async with cms.lifespan_context(None):
            async with httpx.AsyncClient(transport=ASGITransport(app=cms.app), base_url="http://test") as c:
                yield c
    finally:
        os.unlink(db_path)


async def _create(client: httpx.AsyncClient, doc_type: str, body: dict) -> httpx.Response:
    return await client.post("/api/documents", json={"doc_type": doc_type, "body": body})


async def _id(client: httpx.AsyncClient, doc_type: str, body: dict) -> str:
    resp = await _create(client, doc_type, body)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _post(data: list) -> dict:
    return {"title": "October", "description": "Songs", "publish_date": "2026-10-31", "data": data}


async def _song(client: httpx.AsyncClient, track_id: str = "t1") -> str:
    return await _id(
        client,
        "spotify_like",
        {"track_id": track_id, "track_name": "Song", "liked_at": "2026-10-03T12:00:00Z"},
    )


async def _observation(client: httpx.AsyncClient) -> str:
    return await _id(client, "inat_observation", {"observation_id": 1, "observed_on": "2026-07-04"})


async def test_every_slice_kind_saves(client):
    song = await _song(client)
    obs = await _observation(client)
    term = await _id(client, "definition", {"term": "Comp Stomp", "publish_date": "2026-10-01", "definition": "x"})
    link = await _id(
        client, "link", {"title": "A blog", "url": "https://example.com", "date_added": "2026-10-01", "tags": ["developer-blog"]}
    )
    data = [
        {"block_type": "music_range", "from_date": "2026-10-01", "to_date": "2026-10-31"},
        {"block_type": "nature_range", "from_date": "2026-07-04", "to_date": "2026-07-04"},
        {"block_type": "music_picks", "refs": [song]},
        {"block_type": "nature_picks", "refs": [obs]},
        {"block_type": "definition_picks", "refs": [term]},
        {"block_type": "link_picks", "refs": [link]},
        {"block_type": "links_by_tag", "tag": "developer-blog"},
    ]
    resp = await _create(client, "blog_post", _post(data))
    assert resp.status_code == 201, resp.text
    assert resp.json()["body"]["data"] == data


async def test_a_post_without_slices_still_saves(client):
    resp = await _create(client, "blog_post", {"title": "t", "description": "d", "publish_date": "2026-10-01"})
    assert resp.status_code == 201, resp.text


async def test_range_needs_real_dates_in_order(client):
    for from_date, to_date in [("2026-10", "2026-10-31"), ("2026-02-30", "2026-03-01"), ("2026-11-01", "2026-10-01")]:
        resp = await _create(
            client, "blog_post", _post([{"block_type": "music_range", "from_date": from_date, "to_date": to_date}])
        )
        assert resp.status_code == 422, (from_date, to_date, resp.text)


async def test_range_needs_both_dates(client):
    resp = await _create(client, "blog_post", _post([{"block_type": "nature_range", "from_date": "2026-07-04"}]))
    assert resp.status_code == 422


async def test_picks_must_be_the_sources_type(client):
    obs = await _observation(client)
    resp = await _create(client, "blog_post", _post([{"block_type": "music_picks", "refs": [obs]}]))
    assert resp.status_code == 422
    assert "spotify_like" in resp.text


async def test_picks_must_exist(client):
    resp = await _create(client, "blog_post", _post([{"block_type": "nature_picks", "refs": ["nope"]}]))
    assert resp.status_code == 422


async def test_tag_must_be_tag_shaped(client):
    for tag in ["", "Developer Blog", "dev_blog"]:
        resp = await _create(client, "blog_post", _post([{"block_type": "links_by_tag", "tag": tag}]))
        assert resp.status_code == 422, tag


async def test_unknown_slice_kind_is_refused(client):
    resp = await _create(client, "blog_post", _post([{"block_type": "spotify_range", "from_date": "2026-10-01"}]))
    assert resp.status_code == 422


async def test_a_bad_slice_is_refused_on_update_too(client):
    doc_id = await _id(client, "blog_post", _post([]))
    resp = await client.patch(
        f"/api/documents/{doc_id}",
        json={"body": {"data": [{"block_type": "music_range", "from_date": "2026-10-31", "to_date": "2026-10-01"}]}},
    )
    assert resp.status_code == 422


async def test_deleting_a_picked_song_drops_it_from_the_slice(client):
    keep, gone = await _song(client, "t1"), await _song(client, "t2")
    post = await _id(client, "blog_post", _post([{"block_type": "music_picks", "refs": [keep, gone]}]))
    resp = await client.delete(f"/api/documents/{gone}")
    assert resp.status_code in (200, 204), resp.text
    body = (await client.get(f"/api/documents/{post}?draft=true")).json()["body"]
    assert body["data"][0]["refs"] == [keep]


async def test_slice_kinds_are_described_in_the_schema(client):
    schema = (await client.get("/api/schema")).json()
    defs = schema["blog_post"]["schema"]["$defs"]
    described = {d["properties"]["block_type"]["const"]: d.get("description", "") for d in defs.values() if "block_type" in d.get("properties", {})}
    assert set(described) == {
        "music_range", "nature_range", "music_picks", "nature_picks", "definition_picks", "link_picks", "links_by_tag",
    }
    assert all(described.values())


async def test_slice_fields_tell_the_editor_what_to_offer(client):
    # The editor reads these: a date picker for ranges, the link tags for a
    # tag slice, and a definition's term (it has no title) in the picker.
    defs = (await client.get("/api/schema")).json()["blog_post"]["schema"]["$defs"]
    meta = lambda block, field: defs[block]["properties"][field]["cms:field_meta"]  # noqa: E731
    assert meta("MusicRange", "from_date")["field_type"] == "date"
    assert meta("LinksByTag", "tag")["suggest_from"] == "link"
    assert meta("DefinitionPicks", "refs")["ref_label_field"] == "term"


async def test_link_date_added_must_be_a_date(client):
    body = {"title": "A", "url": "https://example.com", "date_added": "Oct 1"}
    assert (await _create(client, "link", body)).status_code == 422
    body["date_added"] = "2026-10-01"
    assert (await _create(client, "link", body)).status_code == 201
