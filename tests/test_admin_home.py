"""The portal is the admin home: cms.joellithgow.com/ (cms/main.py)."""

from __future__ import annotations

import importlib
import json
import sys

import bcrypt
import httpx
import pytest
from httpx import ASGITransport
from starlette_cms.session import generate_session_token

SECRET = "test-session-secret"


@pytest.fixture
def main(tmp_path, monkeypatch):
    """cms.main imported fresh against a scratch database, with one admin user."""
    users = {"joel": bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()}
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'content.db'}")
    monkeypatch.setenv("CHAT_DB_URL", f"sqlite:///{tmp_path / 'chat.db'}")
    monkeypatch.setenv("CMS_SESSION_SECRET", SECRET)
    monkeypatch.setenv("CMS_ADMIN_USERS", json.dumps(users))
    monkeypatch.delenv("MEDIA_BUCKET", raising=False)
    sys.modules.pop("cms.main", None)
    module = importlib.import_module("cms.main")
    yield module
    sys.modules.pop("cms.main", None)


@pytest.mark.asyncio
async def test_home_is_the_portal_behind_the_login(main):
    transport = ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.get("/")
        assert res.status_code == 302
        assert res.headers["location"] == "/api/auth/login?next=/"
        assert (await client.get("/apps.json")).status_code == 401

        client.cookies.set("cms_session", generate_session_token("joel", secret=SECRET))
        home = await client.get("/")
        assert home.status_code == 200
        apps = (await client.get("/apps.json")).json()["apps"]
        assert [a["path"] for a in apps] == ["/editor/", "/gateways/", "/feedback/"]


@pytest.mark.asyncio
async def test_every_admin_page_carries_the_app_bar(main):
    transport = ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        client.cookies.set("cms_session", generate_session_token("joel", secret=SECRET))
        for path in ("/editor/", "/editor/shell", "/gateways/", "/feedback/"):
            res = await client.get(path)
            assert res.status_code == 200, path
            assert '<script src="/app-bar.js" defer></script>' in res.text, path


@pytest.mark.asyncio
async def test_old_and_bare_paths_still_land(main):
    transport = ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        for path, to in (
            ("/admin", "/"),
            ("/admin/", "/"),
            ("/editor", "/editor/"),
            ("/gateways", "/gateways/"),
            ("/feedback", "/feedback/"),
        ):
            res = await client.get(path)
            assert (res.status_code, res.headers["location"]) == (308, to), path
        # The CMS behind the portal still answers its own paths.
        assert (await client.get("/api/auth/login")).status_code == 200
