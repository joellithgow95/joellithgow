"""
Compose the monthly music post: a draft ``blog_post`` showing the songs liked that month.

The post holds no songs, only a ``music_range`` slice over the month (astraeus ADR 026,
section 4), so songs synced after it is made still show up. It is a **draft**: it joins
the default changeset ("Staging") for Joel to read, add to and publish. Nothing here
publishes.

    # last month (the default), as a dry run
    uv run python -m cms.compose_music_month

    # make the draft
    uv run python -m cms.compose_music_month --api-key "$CMS_API_KEY" --apply

    # a given month
    uv run python -m cms.compose_music_month --month 2026-11 --api-key "$CMS_API_KEY" --apply

Run the Spotify sync first so the month is complete (the CronJob in jlithgow-ops does
both, on the 1st):

    PYTHONPATH=. uv run gateways sync spotify-likes --range since_last_sync

Safe to re-run: each month's post carries the import ref ``music:month:YYYY-MM`` (the
posts migrated from the old Spotify months carry the same refs), so a month that already
has a post, draft or published, is left alone. A month with no liked songs gets no post.
"""

from __future__ import annotations

import argparse
import asyncio
import calendar
import os
import sys
from datetime import UTC, date, datetime
from typing import Any

import httpx

PAGE = 100


def month_ref(month: str) -> str:
    """The import ref of the music post for ``month`` (YYYY-MM)."""
    return f"music:month:{month}"


def month_range(month: str) -> tuple[str, str]:
    """First and last day of ``month`` (YYYY-MM), as YYYY-MM-DD."""
    year, mon = (int(x) for x in month.split("-"))
    last = calendar.monthrange(year, mon)[1]
    return f"{month}-01", f"{month}-{last:02d}"


def month_name(month: str) -> str:
    year, mon = (int(x) for x in month.split("-"))
    return f"{calendar.month_name[mon]} {year}"


def previous_month(today: date) -> str:
    first = today.replace(day=1)
    year, mon = (first.year, first.month - 1) if first.month > 1 else (first.year - 1, 12)
    return f"{year}-{mon:02d}"


def music_month_post(month: str, *, title: str | None = None, tags: list[str] | None = None) -> dict[str, Any]:
    """The create-document payload for ``month``'s music post.

    The slug keeps the old Spotify month pages' ``spotify-dump-YYYY-MM``, so a migrated
    month keeps its URL and new months follow the same pattern.
    """
    from_date, to_date = month_range(month)
    return {
        "doc_type": "blog_post",
        "slug": f"spotify-dump-{month}",
        "import_ref": month_ref(month),
        "body": {
            "title": title or f"What I've been listening to — {month_name(month)}",
            "description": f"The songs I liked in {month_name(month)}.",
            "publish_date": from_date,
            "author": "Joel Lithgow",
            "tags": tags if tags is not None else ["music"],
            "data": [{"block_type": "music_range", "from_date": from_date, "to_date": to_date}],
        },
    }


async def documents(http: httpx.AsyncClient, doc_type: str, **query: str) -> list[dict[str, Any]]:
    """Every document of ``doc_type`` matching ``query``, all pages."""
    out: list[dict[str, Any]] = []
    offset = 0
    while True:
        resp = await http.get("/api/documents", params={"type": doc_type, "limit": PAGE, "offset": offset, **query})
        resp.raise_for_status()
        page = resp.json()["documents"]
        out.extend(page)
        if len(page) < PAGE:
            return out
        offset += PAGE


async def find_by_ref(http: httpx.AsyncClient, doc_type: str, ref: str) -> dict[str, Any] | None:
    """The document of ``doc_type`` with import ref ``ref``, published or not."""
    found = await documents(http, doc_type, import_ref=ref)
    return found[0] if found else None


async def likes_in(http: httpx.AsyncClient, month: str) -> int:
    """How many published Spotify likes fall in ``month``."""
    return sum(
        1
        for doc in await documents(http, "spotify_like", published="true")
        if str((doc.get("body") or {}).get("liked_at", ""))[:7] == month
    )


async def run(args: argparse.Namespace, *, http: httpx.AsyncClient | None = None) -> int:
    own = http is None
    headers = {"Authorization": f"Bearer {args.api_key}"} if args.api_key else {}
    http = http or httpx.AsyncClient(base_url=args.cms_url, headers=headers, timeout=60)
    month = args.month or previous_month(datetime.now(UTC).date())
    try:
        print(f"{'APPLY' if args.apply else 'REPORT'} against {args.cms_url}: {month_name(month)}")
        existing = await find_by_ref(http, "blog_post", month_ref(month))
        if existing:
            state = "published" if existing.get("published") else "a draft"
            print(f"{month} already has a post ({existing.get('slug')}, {state}). Nothing to do.")
            return 0
        count = await likes_in(http, month)
        if count == 0:
            print(f"No liked songs in {month}. No post.")
            return 0
        payload = music_month_post(month)
        print(f"{count} liked song(s). Post: {payload['body']['title']!r} at /blog/{payload['slug']}")
        if not args.apply:
            print("Pass --apply to create the draft.")
            return 0
        resp = await http.post("/api/documents", json=payload)
        resp.raise_for_status()
        print(f"Created draft {resp.json()['id']} in the default changeset. It is not published.")
        return 0
    finally:
        if own:
            await http.aclose()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cms-url", default=os.environ.get("CMS_URL", "https://cms.joellithgow.com"))
    ap.add_argument("--api-key", default=os.environ.get("CMS_API_KEY"))
    ap.add_argument("--month", help="YYYY-MM (default: last month, in UTC)")
    ap.add_argument("--apply", action="store_true", help="create the draft (default: report only)")
    args = ap.parse_args()
    if args.month:
        try:
            month_range(args.month)
        except (ValueError, IndexError):
            sys.exit("--month must be YYYY-MM")
    if args.apply and not args.api_key:
        sys.exit("--apply writes, so it needs --api-key (or CMS_API_KEY)")
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
