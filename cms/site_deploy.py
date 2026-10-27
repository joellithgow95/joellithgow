"""
Rebuild the static site after a publish.

The site is moving from Netlify to Cloudflare Workers, built and deployed by the
`deploy-site.yml` GitHub workflow. Until DNS has settled on Cloudflare both hosts
stay live, so a rebuild fires every target that is configured:

- ``NETLIFY_BUILD_HOOK_URL``: Netlify's build hook (a bare POST).
- ``SITE_DEPLOY_TOKEN``: a GitHub fine-grained token with Actions read/write on
  ``joellithgow95/joellithgow`` only. Dispatches the deploy workflow on ``main``.

A CMS webhook can only POST a JSON payload to a URL; it can't send the
Authorization header GitHub needs. So the webhook points back at this app,
``/api/site-deploy?key=<SITE_DEPLOY_HOOK_KEY>``, which dispatches the workflow
itself. The key is the only secret in the URL and all it can do is start a
deploy, the same as Netlify's build hook URL.
"""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse

GITHUB_API = "https://api.github.com"


@dataclass(frozen=True)
class SiteDeployConfig:
    netlify_hook_url: str | None = None
    github_token: str | None = None
    hook_key: str | None = None
    repo: str = "joellithgow95/joellithgow"
    workflow: str = "deploy-site.yml"
    ref: str = "main"

    @classmethod
    def from_env(cls) -> SiteDeployConfig:
        return cls(
            netlify_hook_url=os.environ.get("NETLIFY_BUILD_HOOK_URL") or None,
            github_token=os.environ.get("SITE_DEPLOY_TOKEN") or None,
            hook_key=os.environ.get("SITE_DEPLOY_HOOK_KEY") or None,
        )

    @property
    def configured(self) -> bool:
        """Whether a rebuild would reach anything. Unset in staging/local."""
        return bool(self.netlify_hook_url or self.github_token)


async def dispatch_workflow(config: SiteDeployConfig, client: httpx.AsyncClient) -> int:
    """Start the deploy workflow on ``config.ref``. Returns GitHub's status (204 on success)."""
    resp = await client.post(
        f"{GITHUB_API}/repos/{config.repo}/actions/workflows/{config.workflow}/dispatches",
        headers={
            "Authorization": f"Bearer {config.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={"ref": config.ref},
    )
    return resp.status_code


async def rebuild(config: SiteDeployConfig, client: httpx.AsyncClient) -> dict[str, int]:
    """Fire every configured target. Returns ``{target: HTTP status}``."""
    results: dict[str, int] = {}
    if config.netlify_hook_url:
        results["netlify"] = (await client.post(config.netlify_hook_url)).status_code
    if config.github_token:
        results["github"] = await dispatch_workflow(config, client)
    return results


async def deploy_hook(request: Request, config: SiteDeployConfig) -> JSONResponse:
    """The CMS webhook's target: dispatch the deploy workflow if the key matches.

    404 when the hook isn't configured, so staging/local don't advertise it.
    """
    if not (config.hook_key and config.github_token):
        return JSONResponse({"error": "Not found"}, status_code=404)
    key = request.query_params.get("key", "")
    if not hmac.compare_digest(key.encode(), config.hook_key.encode()):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    async with httpx.AsyncClient(timeout=10) as client:
        status = await dispatch_workflow(config, client)
    return JSONResponse({"ok": status < 400, "status": status}, status_code=200 if status < 400 else 502)
