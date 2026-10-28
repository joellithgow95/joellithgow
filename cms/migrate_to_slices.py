"""
One-off: turn the old Spotify months and iNaturalist outings into blog posts with slices.

The legacy gateways wrote one ``spotify_liked_dump`` per month and one
``inaturalist_outing`` per outing, each holding its songs or observations. Since ADR 026
(astraeus) the songs and sightings are their own data documents (``spotify_like``,
``inat_observation``), and a post shows them through a slice. This makes, for each old
document, a ``blog_post`` that:

- keeps its **slug**, title, publish date and tags, so ``/blog/<slug>`` stays the same
  URL and needs no redirect;
- gets a plain tagline (the old documents have none, and a post needs one);
- shows a month through a ``music_range`` slice over that month, so songs synced late
  still appear, and an outing through a ``nature_picks`` slice of exactly its
  observations, so a day split into two outings (2026-07-04) stays two posts.

Everything goes into one changeset (``--changeset``): the new posts as drafts, and the
deletion of each old document staged next to them. Publishing that changeset swaps old
for new in one step and one site build (astraeus fires a single ``changeset.published``
for it, deletions included). Nothing here publishes.

    report   Read-only. What each old document becomes, and anything that blocks it: an
             observation with no ``inat_observation`` document, a month whose range finds
             no songs, a slug a ``blog_post`` already has, a pending draft on an old doc.
    apply    Creates the drafts and stages the deletions in the changeset (made if no
             open one has that title). Re-runnable: a post already made (found by its
             import ref) is not made again, and a staged deletion is not staged twice.
             Against prod it needs ``--allow-prod``.
    verify   Read-only, after the changeset is published: no old document is left, and
             every migrated post is published and its slice finds something.

    uv run python -m cms.migrate_to_slices report --cms-url https://cms.joellithgow.com
    uv run python -m cms.migrate_to_slices apply  --cms-url https://cms.joellithgow.com \
        --api-key "$CMS_API_KEY" --allow-prod

Stop the legacy gateways first (their entry points are gone from ``pyproject.toml``): a
run after the swap would write the old documents back on the same slugs.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from cms.compose_music_month import documents, month_range, music_month_post

PROD_HOST = "cms.joellithgow.com"
MONTH = "spotify_liked_dump"
OUTING = "inaturalist_outing"
DEFAULT_CHANGESET = "Music and outings as posts"


@dataclass
class Plan:
    old_id: str
    old_type: str
    slug: str
    payload: dict[str, Any]  # the create-document request for the new post
    shows: int  # songs or observations the slice finds now
    was: int  # what the old document held
    problems: list[str] = field(default_factory=list)

    @property
    def ref(self) -> str:
        return self.payload["import_ref"]


def species_tagline(species: list[str], count: int) -> str:
    """"Song Sparrow, Bushtit and 2 more." from the outing's species list."""
    names = [s for s in species if s]
    if not names:
        return f"{count} sighting{'s' if count != 1 else ''}."
    if len(names) <= 3:
        head = names[:-1]
        line = ", ".join(head) + " and " + names[-1] if head else names[0]
    else:
        line = f"{', '.join(names[:2])} and {len(names) - 2} more"
    return line[:1].upper() + line[1:] + "."


def outing_post(doc: dict[str, Any], picks: list[str]) -> dict[str, Any]:
    body = doc.get("body") or {}
    ref = str(doc.get("import_ref") or "").removeprefix("inaturalist:outing:")
    species = [str(s) for s in body.get("species_list") or []]
    count = int(body.get("observation_count") or len(picks))
    return {
        "doc_type": "blog_post",
        "slug": doc["slug"],
        "import_ref": f"nature:outing:{ref or doc['slug']}",
        "body": {
            "title": body.get("title") or body.get("place_guess") or "Outing",
            "description": body.get("description") or species_tagline(species, count),
            "publish_date": body.get("publish_date") or body.get("outing_date"),
            "author": "Joel Lithgow",
            "tags": body.get("tags") or ["nature"],
            "data": [{"block_type": "nature_picks", "refs": picks}],
        },
    }


def month_post(doc: dict[str, Any]) -> dict[str, Any]:
    body = doc.get("body") or {}
    month = str(body.get("publish_date") or "")[:7]
    payload = music_month_post(month, title=body.get("title"), tags=body.get("tags"))
    payload["slug"] = doc["slug"]  # the old URL, whatever the pattern says
    if body.get("description"):
        payload["body"]["description"] = body["description"]
    return payload


async def plan(http: httpx.AsyncClient) -> list[Plan]:
    """What each old month and outing becomes, with its problems."""
    likes = await documents(http, "spotify_like", published="true")
    observations = await documents(http, "inat_observation", published="true")
    by_obs_id = {int(o["body"]["observation_id"]): o["id"] for o in observations}
    posts = await documents(http, "blog_post")
    post_slugs = {p["slug"]: p for p in posts}

    plans: list[Plan] = []
    for doc in await documents(http, MONTH):
        payload = month_post(doc)
        rng = payload["body"]["data"][0]
        shows = sum(1 for x in likes if rng["from_date"] <= str(x["body"].get("liked_at", ""))[:10] <= rng["to_date"])
        p = Plan(doc["id"], MONTH, doc["slug"], payload, shows, int((doc.get("body") or {}).get("song_count") or 0))
        if shows == 0:
            p.problems.append("its month range finds no published spotify_like")
        plans.append(p)

    for doc in await documents(http, OUTING):
        obs = [o for o in (doc.get("body") or {}).get("observations") or [] if isinstance(o, dict)]
        ids = [int(o["id"]) for o in obs if o.get("id") is not None]
        picks = [by_obs_id[i] for i in ids if i in by_obs_id]
        p = Plan(doc["id"], OUTING, doc["slug"], outing_post(doc, picks), len(picks), len(ids))
        missing = [i for i in ids if i not in by_obs_id]
        if missing:
            p.problems.append(f"no inat_observation for observation(s) {missing}")
        if not picks:
            p.problems.append("its picked slice would be empty")
        plans.append(p)

    for p in plans:
        taken = post_slugs.get(p.slug)
        if taken and taken.get("import_ref") != p.ref:
            p.problems.append(f"blog_post {taken['id']} already has the slug")
    old = {d["id"]: d for t in (MONTH, OUTING) for d in await documents(http, t, has_draft="true")}
    for p in plans:
        if p.old_id in old and not old[p.old_id].get("draft_deleted"):
            p.problems.append("the old document has unpublished edits (carry them over by hand)")
    return sorted(plans, key=lambda p: (p.old_type, p.payload["body"]["publish_date"] or "", p.slug))


def render(plans: list[Plan]) -> str:
    lines: list[str] = []
    for kind, label in ((MONTH, "Spotify months"), (OUTING, "iNaturalist outings")):
        group = [p for p in plans if p.old_type == kind]
        lines.append(f"{label}: {len(group)}")
        for p in group:
            b = p.payload["body"]
            unit = "songs" if kind == MONTH else "obs"
            lines.append(f"  /blog/{p.slug}  {b['publish_date']}  {b['title']!r}")
            lines.append(f"      tagline {b['description']!r}  tags {b['tags']}  shows {p.shows} {unit} (held {p.was})")
            lines.extend(f"      PROBLEM: {x}" for x in p.problems)
        lines.append("")
    blocked = sum(1 for p in plans if p.problems)
    lines.append(f"{len(plans)} post(s) to make, {blocked} with problems.")
    return "\n".join(lines)


async def changeset_id(http: httpx.AsyncClient, title: str) -> str:
    resp = await http.get("/api/changesets")
    resp.raise_for_status()
    for cs in resp.json()["changesets"]:
        if cs["title"] == title and cs["status"] == "open":
            return cs["id"]
    resp = await http.post("/api/changesets", json={"title": title})
    resp.raise_for_status()
    return resp.json()["id"]


async def apply(http: httpx.AsyncClient, plans: list[Plan], title: str) -> tuple[int, int]:
    """Make the drafts and stage the deletions in ``title``. Returns (made, staged)."""
    cs = await changeset_id(http, title)
    into = {"x-active-changeset-id": cs}
    made = staged = 0
    for p in plans:
        existing = await documents(http, "blog_post", import_ref=p.ref)
        if not existing:
            resp = await http.post("/api/documents", json=p.payload, headers=into)
            resp.raise_for_status()
            made += 1
        old = await http.get(f"/api/documents/{p.old_id}")
        old.raise_for_status()
        if not old.json().get("draft_deleted"):
            resp = await http.post(f"/api/documents/{p.old_id}/draft-delete", json={"deleted": True}, headers=into)
            resp.raise_for_status()
            staged += 1
    return made, staged


async def verify(http: httpx.AsyncClient) -> list[str]:
    problems: list[str] = []
    for t in (MONTH, OUTING):
        left = await documents(http, t)
        if left:
            problems.append(f"{len(left)} {t} document(s) still exist")
    likes = await documents(http, "spotify_like", published="true")
    observed = {o["id"] for o in await documents(http, "inat_observation", published="true")}
    migrated = [
        p for p in await documents(http, "blog_post")
        if str(p.get("import_ref") or "").startswith(("music:month:", "nature:outing:"))
    ]
    for p in migrated:
        if not p.get("published"):
            problems.append(f"/blog/{p['slug']} is not published")
        for s in (p.get("body") or {}).get("data") or []:
            if s.get("block_type") == "music_range":
                if not any(s["from_date"] <= str(x["body"].get("liked_at", ""))[:10] <= s["to_date"] for x in likes):
                    problems.append(f"/blog/{p['slug']}: its range finds no songs")
            elif s.get("block_type") == "nature_picks":
                gone = [r for r in s.get("refs") or [] if r not in observed]
                if gone:
                    problems.append(f"/blog/{p['slug']}: picks not published: {gone}")
    print(f"{len(migrated)} migrated post(s) found")
    return problems


async def run(args: argparse.Namespace, *, http: httpx.AsyncClient | None = None) -> int:
    host = urlsplit(args.cms_url).hostname or ""
    if args.command == "apply" and host == PROD_HOST and not args.allow_prod:
        print(f"refusing to apply to {PROD_HOST} without --allow-prod", file=sys.stderr)
        return 2
    own = http is None
    headers = {"Authorization": f"Bearer {args.api_key}"} if args.api_key else {}
    http = http or httpx.AsyncClient(base_url=args.cms_url, headers=headers, timeout=60)
    try:
        if args.command == "verify":
            problems = await verify(http)
            for p in problems:
                print(f"STILL WRONG: {p}")
            print("verified clean" if not problems else f"{len(problems)} problem(s)")
            return 1 if problems else 0

        plans = await plan(http)
        print(f"{args.command.upper()} against {args.cms_url}\n")
        print(render(plans))
        if args.command == "report":
            return 1 if any(p.problems for p in plans) else 0
        if any(p.problems for p in plans) and not args.force:
            print("\nrefusing to apply while there are problems (pass --force to apply the rest anyway)")
            return 1
        made, staged = await apply(http, [p for p in plans if not p.problems], args.changeset)
        print(f"\nmade {made} draft post(s), staged {staged} deletion(s) in {args.changeset!r}. Nothing is published.")
        return 0
    finally:
        if own:
            await http.aclose()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("report", "apply", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--cms-url", default=os.environ.get("CMS_URL", f"https://{PROD_HOST}"))
        p.add_argument("--api-key", default=os.environ.get("CMS_API_KEY"))
    a = sub.choices["apply"]
    a.add_argument("--changeset", default=DEFAULT_CHANGESET, help="title of the changeset to fill")
    a.add_argument("--allow-prod", action="store_true", help=f"permit apply against {PROD_HOST}")
    a.add_argument("--force", action="store_true", help="apply the documents without problems")
    args = ap.parse_args()
    if args.command == "apply" and not args.api_key:
        sys.exit("apply writes, so it needs --api-key (or CMS_API_KEY)")
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
