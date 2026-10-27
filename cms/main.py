"""
CMS application entrypoint.

Wires the CMS instance, registers document types from schema.py,
and mounts everything under the Starlette app lifespan.
"""
# Bootstrap OTEL before any Starlette objects are constructed —
# StarletteInstrumentor patches at import time.
from astraeus_otel import TelemetryConfig, setup_telemetry

setup_telemetry(TelemetryConfig(service_name="joellithgow-cms"))

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette_cms import CMS
from starlette_cms.auth import check_session_auth, require_auth
from starlette_editor import Editor
from starlette_cms_feedback import FeedbackAdmin
from starlette_cms_gateways.admin import GatewayAdmin
from starlette_chat import ChatAPI, register_editorial_blocks
from starlette_chat.providers.openai import OpenAICompatibleProvider
from astraeus_portal import Portal, PortalApp
from cms.media import MOUNT_PATH as MEDIA_MOUNT_PATH, build_media
from cms.oauth import OAuthSettings, build_oauth_routes
from cms.schema import register_documents
from cms.site_deploy import SiteDeployConfig, deploy_hook, rebuild

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./cms/data/content.db")
API_KEY = os.environ.get("CMS_API_KEY", "dev-secret")

# Session auth for the inline editor.
# CMS_SESSION_SECRET: random secret for signing session tokens.
# CMS_ADMIN_USERS: JSON object mapping username → bcrypt hash, e.g.
#   {"joel": "$2b$12$..."}
SESSION_SECRET = os.environ.get("CMS_SESSION_SECRET")
_admin_users_raw = os.environ.get("CMS_ADMIN_USERS")
ADMIN_USERS: dict[str, str] | None = json.loads(_admin_users_raw) if _admin_users_raw else None

# OAuth for the Claude app's MCP connectors (cms/oauth.py). All three secrets are
# needed; with any missing the OAuth routes are not mounted and /mcp stays
# bearer-token only. OAUTH_CLIENT_ID / OAUTH_CLIENT_SECRET are what you paste into
# the connector's Client ID / Client secret fields.
OAUTH_CLIENT_ID = os.environ.get("OAUTH_CLIENT_ID")
OAUTH_CLIENT_SECRET = os.environ.get("OAUTH_CLIENT_SECRET")
OAUTH_ISSUER = os.environ.get("OAUTH_ISSUER", "https://cms.joellithgow.com")
OAUTH_SIGNING_SECRET = os.environ.get("OAUTH_SIGNING_SECRET")

# CORS origins for the inline editor embed script.
# Allows the Astro site to make credentialed cross-origin requests to the CMS.
# CMS_CORS_ORIGINS: comma-separated list, e.g. "http://localhost:4321,https://joellithgow.com"
_cors_raw = os.environ.get("CMS_CORS_ORIGINS", "")
CORS_ORIGINS: list[str] = [o.strip() for o in _cors_raw.split(",") if o.strip()]

# Where a site rebuild goes (cms/site_deploy.py): Netlify's build hook and/or the
# GitHub deploy workflow. Set only where a rebuild should actually fire (prod) —
# leaving both unset in staging/local hides the button and makes /api/rebuild a
# no-op, so those environments can't trigger a production deploy.
SITE_DEPLOY = SiteDeployConfig.from_env()

# ADR 024: apply collab steps on the server instead of trusting the editor's copy of
# the document. Off unless CMS_VERIFY_COLLAB=1, so turning it on (or back off) is a
# config change and a pod restart, not a code change.
VERIFY_COLLAB = os.environ.get("CMS_VERIFY_COLLAB") == "1"

# Where new drafts collect. A post made with create_document (the MCP bot, hermes) and
# an edit that names no changeset join the open changeset with this title, made if none
# is open, so "what is waiting to go live" is one list. Gateway runs name their own
# changeset and are unaffected. Set CMS_DEFAULT_CHANGESET to rename it, or to an empty
# string for the old behaviour (a draft in no changeset until edited).
DEFAULT_CHANGESET = os.environ.get("CMS_DEFAULT_CHANGESET", "Staging") or None

cms = CMS(
    database_url=DATABASE_URL,
    auth="apikey",
    api_key=API_KEY,
    read_auth=False,
    mount_path="/",
    session_secret=SESSION_SECRET,
    admin_users=ADMIN_USERS,
    cors_origins=CORS_ORIGINS,
    verify_collab=VERIFY_COLLAB,
    default_changeset=DEFAULT_CHANGESET,
)

register_documents(cms)
register_editorial_blocks(cms)  # system_prompt + model_config only — chat_session/chat_message go to chat.db


async def trigger_rebuild(request: Request) -> JSONResponse:
    """Rebuild the static site on demand (Netlify and/or the GitHub deploy workflow).

    Backs the editor's manual "Rebuild site" button. Guarded by the standard
    write auth (session cookie or API key). Returns 503 when no target is
    configured, which is how staging/local decline to trigger a prod deploy.
    """
    if (err := await require_auth(request, cms)) is not None:
        return err

    if not SITE_DEPLOY.configured:
        return JSONResponse({"error": "Rebuild not configured"}, status_code=503)

    async with httpx.AsyncClient(timeout=10) as client:
        results = await rebuild(SITE_DEPLOY, client)

    return JSONResponse({"ok": all(s < 400 for s in results.values()), "status": results})


# Register /api/rebuild before cms.app is built (extension routes are frozen once
# the app is first accessed).
cms.register_extension_route(
    path="/api/rebuild",
    endpoint=trigger_rebuild,
    methods=["POST"],
    name="rebuild",
)

async def site_deploy_hook(request: Request) -> JSONResponse:
    return await deploy_hook(request, SITE_DEPLOY)


# Target of the CMS webhook that redeploys the site on publish. Authenticated by
# the key in its URL, not the API key: a webhook can't send headers.
cms.register_extension_route(
    path="/api/site-deploy",
    endpoint=site_deploy_hook,
    methods=["POST"],
    name="site_deploy",
)

# Expose the "Rebuild site" toolbar button only where a target is set (prod),
# so staging/local editors don't see a button that can't do anything.
rebuild_actions = (
    [
        {
            "label": "Rebuild site",
            "icon": "🚀",
            "endpoint": "/api/rebuild",
            "confirm": "Trigger a production site rebuild?",
        }
    ]
    if SITE_DEPLOY.configured
    else []
)

# Gate the admin shells behind session auth. Without a guard the /shell pages
# embed the CMS api_key in their HTML and are served to anyone — the guard
# requires a valid cms_session cookie (login at /api/auth/login) instead.
# Media library (cms/media.py). None unless MEDIA_BUCKET is set.
media = build_media(cms)

editor = Editor(
    cms=cms,
    actions=rebuild_actions,
    auth=lambda request: check_session_auth(request, cms),
    # Enables the image picker on ImageFields. The blog `image` is a JSONField, so
    # it gets no picker: paste a media URL into it (see schema.py).
    media_base=MEDIA_MOUNT_PATH if media else None,
)
gateway_admin = GatewayAdmin(cms=cms, auth=lambda request: check_session_auth(request, cms))

# Feedback and bug reports for Astraeus. Reports live in their own SQLite file
# beside content.db (so the volume and Litestream cover it) and are only served
# through auth-checked routes — the document API is public-read here.
FEEDBACK_DB_PATH = Path(DATABASE_URL.removeprefix("sqlite:///")).with_name("feedback.db")
feedback_admin = FeedbackAdmin(
    cms=cms,
    db_path=FEEDBACK_DB_PATH,
    auth=lambda request: check_session_auth(request, cms),
)

# LM Studio (local) or OpenAI — set OPENAI_API_KEY + OPENAI_BASE_URL to override.
# Defaults to LM Studio at http://localhost:1234/v1.
portal = Portal(
    apps=[
        PortalApp(
            name="Editor",
            path="/editor/shell",
            description="Create and edit content documents with the ProseMirror visual editor",
            icon="✏️",
        ),
        PortalApp(
            name="Gateways",
            path="/gateways/shell",
            description="Sync data from external services into the CMS",
            icon="🔄",
        ),
        PortalApp(
            name="Feedback",
            path="/feedback/shell",
            description="File and triage feedback and bug reports for Astraeus",
            icon="🐛",
        ),
        PortalApp(
            name="Chat",
            path="/chat/shell",
            description="AI-powered editing assistant with live collab",
            icon="💬",
        ),
    ],
    title="Joel Lithgow",
    header_links={
        "Site": "https://joellithgow.com",
    },
)

_chat_base_url = os.environ.get("OPENAI_BASE_URL", "http://localhost:1234/v1")
_chat_api_key = os.environ.get("OPENAI_API_KEY", "lm-studio")
_chat_model = os.environ.get("CHAT_MODEL", "local-model")
_cms_base_url = os.environ.get("CMS_BASE_URL", "http://localhost:8000")

# Separate SQLite DB for chat sessions + messages — keeps runtime state out of
# the CMS document table (and out of the changeset panel).
# Note: chat.db is NOT included in the make backup / make db-sync flow; chat
# history is ephemeral. If persistent history matters later, extend
# scripts/backup-prod-db.sh to also cp chat.db alongside content.db.
CHAT_DB_URL = os.environ.get(
    "CHAT_DB_URL", "sqlite:////app/joellithgow/cms/data/chat.db"
)

chat = ChatAPI(
    cms_base_url=_cms_base_url,
    cms_api_key=API_KEY,
    provider=OpenAICompatibleProvider(
        base_url=_chat_base_url,
        api_key=_chat_api_key,
        default_model=_chat_model,
    ),
    session_db_url=CHAT_DB_URL,
    session_secret=SESSION_SECRET,  # validates cms_session cookie — no API key in HTML
    cors_origins=CORS_ORIGINS,      # same origins as the CMS so cross-origin cookie auth works
)

@asynccontextmanager
async def lifespan(app):
    # Init chat.db tables before the first request
    if chat._store is not None:
        await chat._store.init_db()
    async with cms.lifespan_context(app):
        if media is None:
            yield
        else:
            async with media.lifespan_context(app):
                yield

# Starlette's Mount only matches "/admin/" and below, and the bare "/admin"
# falls through to the catch-all CMS mount, which 404s. A redirect makes the
# path people actually type work.
async def _admin_redirect(request: Request) -> RedirectResponse:
    return RedirectResponse("/admin/", status_code=308)


oauth_routes = (
    build_oauth_routes(
        OAuthSettings(
            client_id=OAUTH_CLIENT_ID,
            client_secret=OAUTH_CLIENT_SECRET,
            issuer=OAUTH_ISSUER,
            session_secret=SESSION_SECRET,
            signing_secret=OAUTH_SIGNING_SECRET,
        )
    )
    if OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET and OAUTH_SIGNING_SECRET and SESSION_SECRET
    else []
)

app = Starlette(
    routes=[
        *oauth_routes,
        Route("/admin", endpoint=_admin_redirect, methods=["GET"]),
        Mount("/admin", app=portal.app),
        Mount("/editor", app=editor.app),
        Mount("/gateways", app=gateway_admin.app),
        Mount("/feedback", app=feedback_admin.app),
        Mount("/chat", app=chat.app),
        # Above the catch-all, or the CMS answers /media first.
        *([Mount(MEDIA_MOUNT_PATH, app=media.app)] if media else []),
        Mount("/", app=cms.app),
    ],
    lifespan=lifespan,
)