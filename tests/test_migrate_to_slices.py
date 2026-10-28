"""
Turning the old Spotify months and iNaturalist outings into posts with slices, and
composing the monthly music post, against a real in-process CMS with the site's schema.

Run with: uv run --with pytest --with pytest-asyncio --with respx python -m pytest tests/
"""

from __future__ import annotations

import argparse
import os
import tempfile
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette_cms import CMS

from cms import compose_music_month as compose
from cms import migrate_to_slices as migrate
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


async def sighting(http: httpx.AsyncClient, obs_id: int, day: str, name: str) -> str:
    return await live(
        http, "inat_observation", f"inat-observation-{obs_id}",
        {"title": name, "observation_id": obs_id, "observed_on": day, "common_name": name},
        f"inaturalist:observation:{obs_id}",
    )


async def seed(http: httpx.AsyncClient) -> dict[str, str]:
    """Prod's shape: one month, and one day split into two outings."""
    ids = {
        "song-a": await like(http, "a", "2026-09-03T10:00:00Z"),
        "song-b": await like(http, "b", "2026-09-30T23:00:00Z"),
        "obs-1": await sighting(http, 1, "2026-07-04", "Barn Swallow"),
        "obs-2": await sighting(http, 2, "2026-07-04", "arum lily"),
        "obs-3": await sighting(http, 3, "2026-07-04", "Spotted Cucumber Beetle"),
    }
    ids["month"] = await live(
        http, "spotify_liked_dump", "spotify-dump-2026-09",
        {"title": "What I've been listening to — September 2026", "publish_date": "2026-09-01",
         "song_count": 1, "songs": [{"track_name": "a"}], "tags": ["music"]},
        "spotify:dump:2026-09",
    )
    outing = {"publish_date": "2026-07-04", "outing_date": "2026-07-04"}
    ids["albany"] = await live(
        http, "inaturalist_outing", "nature-outing-2026-07-04",
        {**outing, "title": "Bay Trail, Albany (renamed by hand)", "species_list": ["Barn Swallow", "arum lily"],
         "observation_count": 2, "observations": [{"id": 1}, {"id": 2}], "tags": ["birds", "plants"]},
        "inaturalist:outing:2026-07-04",
    )
    ids["oakland"] = await live(
        http, "inaturalist_outing", "nature-outing-2026-07-04-2",
        {**outing, "title": "Hanover Ave, Oakland", "species_list": ["Spotted Cucumber Beetle"],
         "observation_count": 1, "observations": [{"id": 3}], "tags": ["insects"]},
        "inaturalist:outing:2026-07-04:2",
    )
    return ids


def args(command: str, **kw) -> argparse.Namespace:
    base = {"command": command, "cms_url": "http://testserver", "api_key": "k",
            "changeset": migrate.DEFAULT_CHANGESET, "allow_prod": False, "force": False}
    return argparse.Namespace(**{**base, **kw})


async def posts(http: httpx.AsyncClient) -> dict[str, dict]:
    return {p["slug"]: p for p in await compose.documents(http, "blog_post")}


async def test_plan_keeps_slugs_titles_and_tags_and_picks_each_outings_own_observations(http):
    ids = await seed(http)

    plans = {p.slug: p for p in await migrate.plan(http)}

    assert set(plans) == {"spotify-dump-2026-09", "nature-outing-2026-07-04", "nature-outing-2026-07-04-2"}
    month = plans["spotify-dump-2026-09"].payload["body"]
    assert month["title"] == "What I've been listening to — September 2026"
    assert month["data"] == [{"block_type": "music_range", "from_date": "2026-09-01", "to_date": "2026-09-30"}]
    assert plans["spotify-dump-2026-09"].shows == 2  # the range finds the late-synced song too
    albany = plans["nature-outing-2026-07-04"].payload["body"]
    assert albany["title"] == "Bay Trail, Albany (renamed by hand)"
    assert albany["tags"] == ["birds", "plants"]
    assert albany["data"] == [{"block_type": "nature_picks", "refs": [ids["obs-1"], ids["obs-2"]]}]
    assert albany["description"] == "Barn Swallow and arum lily."
    assert plans["nature-outing-2026-07-04-2"].payload["body"]["data"][0]["refs"] == [ids["obs-3"]]
    assert all(not p.problems for p in plans.values())


async def test_plan_flags_an_observation_with_no_data_document(http):
    await live(
        http, "inaturalist_outing", "nature-outing-2022-05-01",
        {"title": "Rockridge", "publish_date": "2022-05-01", "outing_date": "2022-05-01",
         "observations": [{"id": 99}]},
        "inaturalist:outing:2022-05-01",
    )

    [p] = await migrate.plan(http)

    assert any("99" in x for x in p.problems)
    assert await migrate.run(args("apply"), http=http) == 1  # refuses while there are problems
    assert await posts(http) == {}


async def test_apply_fills_one_changeset_and_publishing_it_swaps_old_for_new_in_one_webhook(http):
    ids = await seed(http)

    assert await migrate.run(args("apply"), http=http) == 0

    sets = (await http.get("/api/changesets", params={"include_documents": "true"})).json()["changesets"]
    [cs] = [c for c in sets if c["title"] == migrate.DEFAULT_CHANGESET]
    held = {d["id"] for d in cs["documents"]}
    new = await posts(http)
    assert set(new) == {"spotify-dump-2026-09", "nature-outing-2026-07-04", "nature-outing-2026-07-04-2"}
    assert held == {p["id"] for p in new.values()} | {ids["month"], ids["albany"], ids["oakland"]}
    assert not any(p["published"] for p in new.values())
    assert not any(c["title"] == "Staging" and c["documents"] for c in sets)  # nothing leaked into Staging
    assert await migrate.run(args("verify"), http=http) == 1  # not published yet

    # A second apply makes nothing new.
    assert await migrate.run(args("apply"), http=http) == 0
    assert len(await posts(http)) == 3

    changeset_events: list[str] = []
    document_events: list[str] = []

    async def on_changeset(cms, event, *a, **kw):
        changeset_events.append(event)

    async def on_document(cms, event, *a, **kw):
        document_events.append(event)

    import asyncio

    with (
        patch("starlette_cms.api.changesets._fire_changeset_webhook", new=on_changeset),
        patch("starlette_cms.api.webhooks.fire_event", new=on_document),
    ):
        assert (await http.post(f"/api/changesets/{cs['id']}/publish")).status_code == 200
        await asyncio.sleep(0)

    assert changeset_events == ["changeset.published"]
    assert document_events == []  # so one site build, not one per deleted document
    for old in (ids["month"], ids["albany"], ids["oakland"]):
        assert (await http.get(f"/api/documents/{old}")).status_code == 404
    assert all(p["published"] for p in (await posts(http)).values())
    assert await migrate.run(args("verify"), http=http) == 0


async def test_apply_refuses_prod_without_the_flag(http):
    assert await migrate.run(args("apply", cms_url="https://cms.joellithgow.com"), http=http) == 2


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


async def test_compose_skips_a_month_that_already_has_its_migrated_post(http):
    await seed(http)
    assert await migrate.run(args("apply"), http=http) == 0

    assert await compose.run(compose_args(), http=http) == 0

    assert len([p for p in (await posts(http)).values() if p["slug"].startswith("spotify-dump-")]) == 1


async def test_compose_makes_nothing_for_a_month_with_no_likes(http):
    assert await compose.run(compose_args(month="2026-08"), http=http) == 0
    assert await posts(http) == {}


def test_previous_month_and_ranges():
    from datetime import date

    assert compose.previous_month(date(2026, 12, 1)) == "2026-11"
    assert compose.previous_month(date(2027, 1, 1)) == "2026-12"
    assert compose.month_range("2028-02") == ("2028-02-01", "2028-02-29")
