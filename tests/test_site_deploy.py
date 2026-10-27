"""The site rebuild targets and the webhook relay to the GitHub deploy workflow."""

from __future__ import annotations

import httpx
import pytest
import respx
from starlette.applications import Starlette
from starlette.routing import Route

from cms.site_deploy import SiteDeployConfig, deploy_hook, rebuild

DISPATCH = "https://api.github.com/repos/joellithgow95/joellithgow/actions/workflows/deploy-site.yml/dispatches"
NETLIFY = "https://api.netlify.com/build_hooks/HOOK"


def app_for(config: SiteDeployConfig) -> httpx.AsyncClient:
    async def hook(request):
        return await deploy_hook(request, config)

    app = Starlette(routes=[Route("/api/site-deploy", hook, methods=["POST"])])
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cms")


@pytest.mark.asyncio
async def test_rebuild_fires_every_configured_target():
    config = SiteDeployConfig(netlify_hook_url=NETLIFY, github_token="TOKEN")
    with respx.mock:
        netlify = respx.post(NETLIFY).respond(200)
        github = respx.post(DISPATCH).respond(204)
        async with httpx.AsyncClient() as client:
            assert await rebuild(config, client) == {"netlify": 200, "github": 204}
    assert netlify.called
    sent = github.calls.last.request
    assert sent.headers["Authorization"] == "Bearer TOKEN"
    assert sent.content == b'{"ref":"main"}'


@pytest.mark.asyncio
async def test_rebuild_skips_unset_targets():
    assert not SiteDeployConfig().configured
    with respx.mock:
        respx.post(DISPATCH).respond(204)
        async with httpx.AsyncClient() as client:
            assert await rebuild(SiteDeployConfig(github_token="T"), client) == {"github": 204}


@pytest.mark.asyncio
async def test_hook_dispatches_with_the_right_key():
    config = SiteDeployConfig(github_token="T", hook_key="KEY")
    with respx.mock:
        github = respx.post(DISPATCH).respond(204)
        async with app_for(config) as client:
            resp = await client.post("/api/site-deploy?key=KEY", json={"event": "document.published"})
    assert resp.status_code == 200 and resp.json() == {"ok": True, "status": 204}
    assert github.called


@pytest.mark.asyncio
async def test_hook_refuses_a_wrong_key():
    config = SiteDeployConfig(github_token="T", hook_key="KEY")
    with respx.mock:
        github = respx.post(DISPATCH).respond(204)
        async with app_for(config) as client:
            assert (await client.post("/api/site-deploy?key=nope")).status_code == 403
            assert (await client.post("/api/site-deploy")).status_code == 403
    assert not github.called


@pytest.mark.asyncio
async def test_hook_is_absent_when_unconfigured():
    async with app_for(SiteDeployConfig(github_token="T")) as client:
        assert (await client.post("/api/site-deploy?key=")).status_code == 404


@pytest.mark.asyncio
async def test_hook_reports_a_failed_dispatch():
    config = SiteDeployConfig(github_token="T", hook_key="KEY")
    with respx.mock:
        respx.post(DISPATCH).respond(401)
        async with app_for(config) as client:
            resp = await client.post("/api/site-deploy?key=KEY")
    assert resp.status_code == 502 and resp.json()["status"] == 401
