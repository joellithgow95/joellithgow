"""The sync_gateway MCP tool: range arguments, the CMS-held cursor, and what it tells the caller."""

from __future__ import annotations

import httpx
import pytest
from starlette_cms_gateways.client import CMSClient

from cms import gateway_mcp_server as srv
from cms.gateways.inaturalist_observations import INaturalistObservationsGateway
from gateway_fakes import PARK, raw_obs


@pytest.fixture
def wired(env, api, monkeypatch):
    client, store, transport = env

    def fresh_client() -> CMSClient:
        # Like prod: each tool call builds, uses and closes its own client.
        http = httpx.AsyncClient(transport=transport, base_url="http://testserver")
        return CMSClient(base_url="http://testserver", _http_client=http)

    monkeypatch.setattr(srv, "_get_client", fresh_client)
    # The tool reads discover_gateways from the server module; the CMS's gateway API
    # (which serves the cursor) resolves gateway names with its own copy.
    gateways = {"inaturalist-observations": INaturalistObservationsGateway}
    monkeypatch.setattr(srv, "discover_gateways", lambda: gateways)
    monkeypatch.setattr("starlette_cms_gateways.admin.api.discover_gateways", lambda: gateways)
    return client, api, transport


@pytest.mark.asyncio
async def test_first_call_reports_that_it_synced_everything_then_goes_incremental(wired):
    _, api, _ = wired
    api.db = [raw_obs(1, "2026-05-03", *PARK)]

    first = await srv.sync_gateway("inaturalist-observations")
    second = await srv.sync_gateway("inaturalist-observations")

    assert "all_time" in first and "no cursor yet" in first and "Created: 1" in first
    assert "since_last_sync" in second and "no cursor yet" not in second
    assert "Created: 0" in second and "Updated: 0" in second


@pytest.mark.asyncio
async def test_custom_range_is_passed_through(wired):
    _, api, _ = wired
    api.db = [raw_obs(1, "2026-04-01", *PARK), raw_obs(2, "2026-05-03", *PARK)]

    out = await srv.sync_gateway(
        "inaturalist-observations", range="custom", from_date="2026-05-01", to_date="2026-05-31"
    )

    assert "custom" in out and "Created: 1" in out
    assert (api.requests[0]["d1"], api.requests[0]["d2"]) == ("2026-05-01", "2026-05-31")


@pytest.mark.asyncio
async def test_dates_alone_imply_a_custom_range(wired):
    _, api, _ = wired
    api.db = [raw_obs(2, "2026-05-03", *PARK)]

    out = await srv.sync_gateway("inaturalist-observations", from_date="2026-05-01")

    assert "custom" in out and api.requests[0]["d1"] == "2026-05-01"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [{"range": "sometimes"}, {"range": "custom"}, {"range": "all_time", "from_date": "2026-01-01"}],
)
async def test_bad_range_is_refused_before_anything_runs(wired, kwargs):
    _, api, transport = wired
    transport_calls = len(transport.calls)

    out = await srv.sync_gateway("inaturalist-observations", **kwargs)

    assert out.startswith("❌ Invalid range")
    assert api.requests == [] and len(transport.calls) == transport_calls


@pytest.mark.asyncio
async def test_the_cursor_the_tool_writes_is_the_one_the_cms_serves_and_the_run_is_in_history(wired, env):
    from datetime import datetime

    client, store, _ = env
    _, api, _ = wired
    api.db = [raw_obs(1, "2026-05-03", *PARK)]

    await srv.sync_gateway("inaturalist-observations")

    served = (await client._get_http().get(
        "http://testserver/api/gateways/inaturalist-observations/cursor"
    )).json()["cursor"]
    assert served and datetime.fromisoformat(served) == await store.get_cursor("inaturalist-observations")
    jobs = await store.list_for_gateway("inaturalist-observations")
    assert [j["status"] for j in jobs] == ["done"], "an MCP run is in the job history"
    assert jobs[0]["result"]["created"] == 1
    assert await store.get_last_synced("inaturalist-observations") is not None


@pytest.mark.asyncio
async def test_recent_items_are_bounded_summaries_without_bodies(wired):
    _, api, _ = wired
    api.db = [raw_obs(i, f"2026-05-{i:02d}", *PARK) for i in range(1, 4)]
    await srv.sync_gateway("inaturalist-observations")

    out = await srv.get_recent_gateway_items("inat_observation", limit=2)

    assert "3 document(s)" in out and "(showing 2)" in out
    assert "photo_urls" not in out and "taxon_name" not in out, "no bodies in a list"
    assert "slug" in out and "title" in out
    assert (await srv.get_recent_gateway_items("inat_observation", limit=500)).startswith("❌ limit")


@pytest.mark.asyncio
async def test_a_long_deferred_list_is_capped_in_the_reply():
    result = {
        "gateway": "g", "range": {"mode": "all_time"}, "created": 0, "updated": 0, "skipped": 0,
        "errors": 0, "error_details": [], "deferred": [f"ref:{i}" for i in range(srv.MAX_NAMED + 5)],
    }
    lines = srv._named([f"`{r}`" for r in result["deferred"]])
    assert len(lines) == srv.MAX_NAMED + 1 and lines[-1].endswith("…and 5 more")


def test_tools_are_annotated_and_reject_unknown_arguments():
    tools = {t.name: t for t in srv.mcp._tool_manager.list_tools()}
    assert tools["get_recent_gateway_items"].annotations.readOnlyHint is True
    sync = tools["sync_gateway"].annotations
    assert (sync.readOnlyHint, sync.destructiveHint, sync.openWorldHint) == (False, False, True)
    from pydantic import ValidationError

    model = tools["sync_gateway"].fn_metadata.arg_model
    with pytest.raises(ValidationError, match="Did you mean `gateway_name`"):
        model.model_validate({"gateway": "x"})
