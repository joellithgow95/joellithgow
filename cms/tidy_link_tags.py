"""
One-off: tidy the tags on link documents, and move tag slices to a tag list.

A link collection is a post with a ``links_by_tag`` slice, and its list is every link
carrying any of the slice's tags, so a link's tags decide which collections it is in.
This renames and drops near-duplicate tags (``RENAME``), adds the tags a link is missing
(``ADD``, by slug), and rewrites slices saved as ``{"tag": ...}`` to ``{"tags": [...]}``.

    # what would change (writes nothing)
    uv run python -m cms.tidy_link_tags --cms-url https://cms.joellithgow.com

    # write the changes as drafts in one changeset (needs the API key)
    uv run python -m cms.tidy_link_tags --cms-url https://cms.joellithgow.com \
        --api-key "$CMS_API_KEY" --apply

``--apply`` writes drafts only, into the open changeset called ``--changeset`` (made if
none is open), so the site changes when that changeset is published. It reads each
document's draft when it has one, so a pending edit is kept. Safe to re-run: a second
run finds nothing to change.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from typing import Any

import httpx

PAGE = 100

# Tag -> the tag it becomes, or None to drop it. Tags not listed are kept.
RENAME: dict[str, str | None] = {
    "design-portfolio": "design",
    "visual-design": "design",
    "design-collective": "community",
    "collective": "community",
    "inspiration": "inspo",  # the Honeyguide book joins the Inspo folder
    "development": None,  # every developer blog is already developer-blogs
    "tech-blog": None,
}

# Link slug -> tags it should carry.
ADD: dict[str, list[str]] = {
    "rebecca-powells-blog": ["developer-blogs"],
}


@dataclass
class Change:
    id: str
    doc_type: str
    slug: str
    field: str
    before: Any
    after: Any


def tidy_tags(tags: list[str], add: list[str] = ()) -> list[str]:
    """``tags`` renamed by ``RENAME``, plus ``add``, in order, without repeats."""
    out: list[str] = []
    for tag in [*tags, *add]:
        tag = RENAME.get(tag, tag)
        if tag and tag not in out:
            out.append(tag)
    return out


def tidy_slices(data: list[Any]) -> list[Any]:
    """``data`` with every ``links_by_tag`` slice holding a ``tags`` list."""
    out = []
    for s in data:
        if isinstance(s, dict) and s.get("block_type") == "links_by_tag" and "tag" in s:
            s = {k: v for k, v in s.items() if k != "tag"} | {"tags": s.get("tags") or [s["tag"]]}
        out.append(s)
    return out


async def _documents(http: httpx.AsyncClient, doc_type: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    offset = 0
    while True:
        resp = await http.get("/api/documents", params={"type": doc_type, "limit": PAGE, "offset": offset})
        resp.raise_for_status()
        page = resp.json()["documents"]
        out.extend(page)
        if len(page) < PAGE:
            return out
        offset += PAGE


async def _body(http: httpx.AsyncClient, doc: dict[str, Any]) -> dict[str, Any]:
    """The draft body when there is one (so a pending edit is kept), else the stored body."""
    if not doc.get("has_draft"):
        return doc["body"]
    resp = await http.get(f"/api/documents/{doc['id']}", params={"draft": "true"})
    resp.raise_for_status()
    return resp.json()["body"]


async def find_changes(http: httpx.AsyncClient) -> list[Change]:
    changes: list[Change] = []
    for doc in await _documents(http, "link"):
        before = (await _body(http, doc)).get("tags") or []
        after = tidy_tags(before, ADD.get(doc.get("slug", ""), []))
        if after != before:
            changes.append(Change(doc["id"], "link", doc.get("slug", ""), "tags", before, after))
    for doc in await _documents(http, "blog_post"):
        before = (await _body(http, doc)).get("data") or []
        after = tidy_slices(before)
        if after != before:
            changes.append(Change(doc["id"], "blog_post", doc.get("slug", ""), "data", before, after))
    return changes


async def changeset_id(http: httpx.AsyncClient, title: str) -> str:
    """The open changeset called ``title``, made if none is open."""
    resp = await http.get("/api/changesets")
    resp.raise_for_status()
    for cs in resp.json()["changesets"]:
        if cs["title"] == title and cs["status"] == "open":
            return cs["id"]
    resp = await http.post("/api/changesets", json={"title": title})
    resp.raise_for_status()
    return resp.json()["id"]


def _show(value: Any) -> str:
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return ", ".join(value) or "(none)"
    return str([s for s in value if isinstance(s, dict) and s.get("block_type") == "links_by_tag"])


async def run(args: argparse.Namespace, *, http: httpx.AsyncClient | None = None) -> int:
    own = http is None
    headers = {"Authorization": f"Bearer {args.api_key}"} if args.api_key else {}
    http = http or httpx.AsyncClient(base_url=args.cms_url, headers=headers, timeout=30)
    try:
        changes = await find_changes(http)
        print(f"{'APPLY' if args.apply else 'REPORT'} against {args.cms_url}")
        if not changes:
            print("Every link's tags and every tag slice are already tidy. Nothing to do.")
            return 0
        for c in changes:
            print(f"  {c.doc_type:<10} {c.slug}\n      {_show(c.before)}\n    → {_show(c.after)}")

        if not args.apply:
            print(f"\n{len(changes)} document(s) would change. Pass --apply to write them as drafts in {args.changeset!r}.")
            return 0

        cs = await changeset_id(http, args.changeset)
        for c in changes:
            resp = await http.patch(
                f"/api/documents/{c.id}", json={"body": {c.field: c.after}}, headers={"X-Active-Changeset-Id": cs}
            )
            resp.raise_for_status()
        print(f"\nWrote {len(changes)} draft(s) to {args.changeset!r}. Nothing is published.")
        return 0
    finally:
        if own:
            await http.aclose()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cms-url", default=os.environ.get("CMS_URL", "https://cms.joellithgow.com"))
    ap.add_argument("--api-key", default=os.environ.get("CMS_API_KEY"))
    ap.add_argument("--changeset", default="Link tags", help="the changeset the drafts go in")
    ap.add_argument("--apply", action="store_true", help="write the drafts (default: report only)")
    args = ap.parse_args()
    if args.apply and not args.api_key:
        sys.exit("--apply writes, so it needs --api-key (or CMS_API_KEY)")
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
