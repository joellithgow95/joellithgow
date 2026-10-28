"""
Tests for the data gateways (ADR 026): one ``spotify_like`` per song and one
``inat_observation`` per sighting. Run with:

    uv run --with pytest --with pytest-asyncio --with respx python -m pytest tests/

As in ``test_gateways.py``, the CMS is real (in-process, the site's own schema);
only iNaturalist (respx) and Spotify (a fake client) are faked.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from gateway_fakes import FAR, PARK, FakeSpotify, library, liked, raw_obs
from starlette_cms_gateways.base import SyncRange, SyncWindow
from starlette_cms_gateways.client import CMSClient

from cms.gateways.inaturalist_observations import (
    OBSERVATION_FIELDS,
    INaturalistObservationsGateway,
    observation_body,
)
from cms.gateways.spotify_likes import (
    LIKE_FIELDS,
    SpotifyLikesGateway,
    curate_like,
    liked_bounds,
)


async def docs_of(client: CMSClient, doc_type: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    offset = 0
    while True:
        page = await client.list_documents(doc_type=doc_type, limit=100, offset=offset)
        out.update({d["import_ref"]: d for d in page["documents"]})
        offset += len(page["documents"])
        if not page["documents"] or offset >= page["total"]:
            return out


def likes_gateway(client, store, fake):
    return SpotifyLikesGateway(cms_client=client, job_store=store, spotify_client=fake)


def observations_gateway(client, store):
    return INaturalistObservationsGateway(cms_client=client, job_store=store)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_data_types_are_filed_under_data(env):
    client, _, _ = env
    schema = (await client._get_http().get("http://testserver/api/schema")).json()
    assert schema["spotify_like"]["group"] == "Data"
    assert schema["inat_observation"]["group"] == "Data"
    assert "group" not in schema["blog_post"]


# ---------------------------------------------------------------------------
# Spotify likes
# ---------------------------------------------------------------------------


def test_curate_like_keeps_what_a_slice_needs_and_no_noise():
    body = curate_like(liked("s1", "2026-09-02T10:00:00Z"))
    assert body == {
        "title": "Song s1 — Artist",
        "track_id": "s1",
        "track_name": "Song s1",
        "artist_name": "Artist",
        "album_name": "Album",
        "album_art_url": "small-s1",
        "spotify_url": "https://open.spotify.com/track/s1",
        "liked_at": "2026-09-02T10:00:00Z",
    }
    assert set(body) == set(LIKE_FIELDS)


def test_a_local_file_has_no_spotify_id_and_is_skipped():
    item = liked("x", "2026-09-02T10:00:00Z")
    item["track"]["id"] = None
    assert curate_like(item) is None


def test_liked_bounds():
    floor = datetime(2025, 1, 1, tzinfo=UTC)
    assert liked_bounds(SyncWindow("all_time")) == (floor, None)
    since = datetime(2026, 9, 19, 12, tzinfo=UTC)
    assert liked_bounds(SyncWindow("since_last_sync", changed_since=since)) == (since, None)
    old = datetime(2024, 6, 1, tzinfo=UTC)
    assert liked_bounds(SyncWindow("since_last_sync", changed_since=old)) == (floor, None)
    custom = SyncWindow("custom", start=date(2024, 12, 1), end=date(2024, 12, 31))
    assert liked_bounds(custom) == (datetime(2024, 12, 1, tzinfo=UTC), date(2024, 12, 31))


@pytest.mark.asyncio
async def test_first_likes_sync_creates_one_published_doc_per_song_above_the_floor(env):
    client, store, transport = env
    transport.calls.clear()

    result = await likes_gateway(client, store, FakeSpotify(library())).sync()

    assert (result.created, result.errors) == (5, [])
    docs = await docs_of(client, "spotify_like")
    assert set(docs) == {f"spotify:like:{t}" for t in ("s2", "s1", "a2", "a1", "j1")}
    s1 = docs["spotify:like:s1"]
    assert s1["published"] and not s1["has_draft"]
    assert s1["slug"] == "spotify-like-s1"
    assert s1["body"]["liked_at"] == "2026-09-02T10:00:00Z"
    publishes = [p for m, p in transport.writes() if p.endswith("/publish")]
    assert publishes == [f"/api/changesets/{result.changeset_id}/publish"], "one run, one build"


@pytest.mark.asyncio
async def test_likes_resync_with_nothing_new_writes_nothing(env):
    client, store, transport = env
    gw = likes_gateway(client, store, FakeSpotify(library()))
    await gw.sync()
    transport.calls.clear()

    result = await gw.sync(SyncRange("all_time"))

    assert (result.created, result.updated, result.skipped) == (0, 0, 5)
    assert transport.writes() == []


@pytest.mark.asyncio
async def test_incremental_likes_sync_adds_the_new_song_and_stops_paging(env):
    client, store, _ = env
    old = [liked(f"o{i}", "2026-03-10T10:00:00Z") for i in range(130)]
    fake = FakeSpotify(library()[:2] + old)
    gw = likes_gateway(client, store, fake)
    await store.set_cursor("spotify_likes", datetime(2026, 9, 20, 12, tzinfo=UTC))
    fake.items.insert(0, liked("s3", "2026-09-25T10:00:00Z"))

    result = await gw.sync()

    assert result.created == 1, "s3 only: s2 is older than the cursor less its one-day overlap"
    assert fake.offsets == [0], "one page reaches past the window"
    assert set(await docs_of(client, "spotify_like")) == {"spotify:like:s3"}


@pytest.mark.asyncio
async def test_custom_likes_range_reaches_before_the_floor_and_leaves_the_cursor(env):
    client, store, _ = env
    gw = likes_gateway(client, store, FakeSpotify(library()))

    result = await gw.sync(SyncRange("custom", date(2024, 12, 1), date(2024, 12, 31)))

    assert result.created == 1
    assert set(await docs_of(client, "spotify_like")) == {"spotify:like:old"}
    assert await store.get_cursor("spotify_likes") is None


@pytest.mark.asyncio
async def test_a_changed_track_overwrites_the_record(env):
    client, store, _ = env
    fake = FakeSpotify([liked("s1", "2026-09-02T10:00:00Z")])
    gw = likes_gateway(client, store, fake)
    await gw.sync()

    fake.items[0]["track"]["album"]["name"] = "Deluxe Edition"
    result = await gw.sync(SyncRange("all_time"))

    assert result.updated == 1
    doc = (await docs_of(client, "spotify_like"))["spotify:like:s1"]
    assert doc["body"]["album_name"] == "Deluxe Edition"
    assert doc["published"] and not doc["has_draft"]


# ---------------------------------------------------------------------------
# iNaturalist observations
# ---------------------------------------------------------------------------


def test_observation_body_is_slim_and_complete():
    body = observation_body(raw_obs(7, "2026-05-03", *PARK))
    assert body is not None
    assert set(body) == set(OBSERVATION_FIELDS)
    assert body["title"] == "American Robin — Prospect Park, Brooklyn, NY"
    assert body["observation_id"] == 7
    assert (body["lat"], body["lon"]) == PARK
    assert body["photo_urls"] == ["https://img.example/7/original.jpg"]
    assert body["tags"] == ["birds"]
    for noise in ("quality_grade", "comments", "identifications", "id"):
        assert noise not in body


def test_an_obscured_observation_has_no_position():
    body = observation_body(raw_obs(8, "2026-05-03", *PARK, obscured=True))
    assert body is not None and body["lat"] is None and body["lon"] is None


def test_an_undated_observation_is_skipped():
    obs = raw_obs(9, "2026-05-03", *PARK)
    obs["observed_on"] = None
    assert observation_body(obs) is None


@pytest.mark.asyncio
async def test_first_observations_sync_creates_one_published_doc_per_sighting(env, api):
    client, store, transport = env
    # Two places on one day: one outing each for the old gateway, but here just sightings.
    api.db = [
        raw_obs(1, "2026-07-04", *PARK, time="09:00"),
        raw_obs(2, "2026-07-04", *FAR, time="15:00", common="Western Fence Lizard",
                name="Sceloporus occidentalis", iconic="Reptilia", place="Albany, CA"),
        raw_obs(3, "2026-07-05", None, None),
    ]
    transport.calls.clear()

    result = await observations_gateway(client, store).sync()

    assert (result.created, result.errors) == (3, [])
    docs = await docs_of(client, "inat_observation")
    assert set(docs) == {f"inaturalist:observation:{i}" for i in (1, 2, 3)}
    lizard = docs["inaturalist:observation:2"]
    assert lizard["published"] and lizard["slug"] == "inat-observation-2"
    assert lizard["body"]["tags"] == ["reptiles"] and lizard["body"]["observed_on"] == "2026-07-04"
    assert docs["inaturalist:observation:3"]["body"].get("lat") is None
    publishes = [p for m, p in transport.writes() if p.endswith("/publish")]
    assert publishes == [f"/api/changesets/{result.changeset_id}/publish"]


@pytest.mark.asyncio
async def test_observations_resync_with_only_upstream_noise_writes_nothing(env, api):
    client, store, transport = env
    api.db = [raw_obs(1, "2026-05-03", *PARK)]
    gw = observations_gateway(client, store)
    await gw.sync()
    api.db[0]["quality_grade"] = "needs_id"
    api.db[0]["faves"] = [{"id": 1}]
    transport.calls.clear()

    result = await gw.sync(SyncRange("all_time"))

    assert (result.created, result.updated, result.skipped) == (0, 0, 1)
    assert transport.writes() == []


@pytest.mark.asyncio
async def test_incremental_observations_sync_asks_for_what_changed_and_updates_it(env, api):
    client, store, _ = env
    api.db = [raw_obs(1, "2026-05-03", *PARK), raw_obs(2, "2026-05-04", *PARK)]
    gw = observations_gateway(client, store)
    await gw.sync()
    await store.set_cursor("inaturalist_observations", datetime(2026, 6, 1, tzinfo=UTC))
    # A re-identification of an old sighting, months after it was observed.
    api.db[0].update(
        taxon={"name": "Turdus", "preferred_common_name": "Thrush", "iconic_taxon_name": "Aves"},
        updated_at="2026-06-10T00:00:00+00:00",
    )
    api.requests.clear()

    result = await gw.sync()

    assert (result.updated, result.skipped, result.created) == (1, 0, 0)
    assert [r.get("updated_since") for r in api.requests] == ["2026-05-31T00:00:00+00:00"]
    doc = (await docs_of(client, "inat_observation"))["inaturalist:observation:1"]
    assert doc["body"]["common_name"] == "Thrush" and doc["body"]["title"].startswith("Thrush")


@pytest.mark.asyncio
async def test_a_sighting_that_becomes_obscured_loses_its_position(env, api):
    client, store, _ = env
    api.db = [raw_obs(1, "2026-05-03", *PARK)]
    gw = observations_gateway(client, store)
    await gw.sync()
    api.db[0]["obscured"] = True

    result = await gw.sync(SyncRange("all_time"))

    assert result.updated == 1
    body = (await docs_of(client, "inat_observation"))["inaturalist:observation:1"]["body"]
    assert body.get("lat") is None and body.get("lon") is None


@pytest.mark.asyncio
async def test_custom_observations_range_filters_by_observed_date(env, api):
    client, store, _ = env
    api.db = [raw_obs(i, f"2026-05-0{i}", *PARK) for i in range(1, 5)]

    result = await observations_gateway(client, store).sync(
        SyncRange("custom", date(2026, 5, 2), date(2026, 5, 3))
    )

    assert result.created == 2
    assert set(await docs_of(client, "inat_observation")) == {
        "inaturalist:observation:2",
        "inaturalist:observation:3",
    }
    assert await store.get_cursor("inaturalist_observations") is None
