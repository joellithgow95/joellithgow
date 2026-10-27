# joellithgow.com — Claude Code context

Personal portfolio site for Joel Lithgow. Astro SSG frontend + self-hosted Astraeus CMS on a k3s cluster (`jlithgow-ops`). EC2 is a fallback until retired.

---

## Where prod runs

Since 2026-10-01 prod (`cms.joellithgow.com`) runs in the k3s cluster, managed in `~/Code/personal/jlithgow-ops` (`cluster/apps/astraeus-cms/`), **not on EC2**. EC2 still runs the old prod stack as a fallback and serves `cms-staging`, but gets no prod traffic and its data is stale.

- **Deploy CMS code:** merge to `main` (`build-cms.yml` pushes `ghcr.io/asneakytoast/joellithgow-cms:jl-<joellithgow sha>-astraeus-<astraeus sha>`), then bump the image pin in jlithgow-ops (`deployment.yaml`, `mcp.yaml`) to that tag and merge. Pin the `jl-…` tag, not `astraeus-<sha>`: that one only changes with astraeus, so a pin on it keeps the node's cached image. Argo syncs it. Never `make prod-deploy` for this — it targets EC2.
- **Config / secrets:** `secrets.enc.yaml` (sops) in jlithgow-ops. Pods read it as plain env vars, so after a change run `kubectl -n astraeus rollout restart deploy/astraeus-cms`.
- **Collab verification (ADR 024):** `CMS_VERIFY_COLLAB=1` makes the editor's WebSocket apply every step on the server instead of trusting the editor's copy of the document, refuse steps that break the schema, and serve catch-up after a reconnect. Off by default; set it in the pod env in jlithgow-ops and restart. It refuses a rich-text field whose stored JSON does not fit the editor's schema (for example a mark the schema lacks), so edits to such a document fail until it is fixed. Turn it back off by unsetting the variable.
- **Backups:** Litestream streams `content.db` to R2 (`jlithgow-ops-backups`, `astraeus-cms/prod/content.db`) and restores it into an empty volume on pod start. It replicates the whole file, so the gateway sync state (cursor and job history; see *Gateways*) is in the backup too.
- **EC2-only `make` targets** (`db-sync`, `backup`, `prod-deploy`, `prod-restart`, `mcp-deploy`, `caddy-deploy`, `cron-install`, `staging-*`) act on the fallback box. `make db-sync` pulls EC2's *stale* backup, not live prod, until it is repointed at R2.

---

## Key commands

```bash
make dev              # start local CMS + Astro HMR (run db-sync first if DB is stale)
make db-sync          # pull latest prod backup from EC2 → restore local CMS container
make backup           # trigger a prod DB backup on EC2 right now
make staging-restore  # restore latest backup into EC2 staging + restart it
make staging-restart  # restart staging container only (no DB change)
make ssh              # ssh joellithgow-cms shortcut
make webhooks         # list registered CMS webhooks across prod + staging
make mcp-deploy       # build + start the MCP sidecars on EC2 (content :8002, gateway :8003)
make caddy-deploy     # ship Caddyfile to EC2 + reload

bun run build         # production Astro build
bun run preview       # preview the build locally
```

## Architecture

```
Netlify (Astro SSG)
  └── fetches from ASTRAEUS_URL at build time
        ├── local dev:  http://localhost:8001  (docker-compose.local.yml)
        ├── staging:    https://cms-staging.joellithgow.com  (:8001 on EC2)
        └── prod:       https://cms.joellithgow.com  (k3s cluster)

k3s cluster (jlithgow-ops, namespace astraeus) — Cloudflare Tunnel into ClusterIP services:
  ├── astraeus-cms     → :8000 → cms.joellithgow.com   (Litestream sidecar → R2, continuous)
  └── mcp-proxy        → :8080 → cms.joellithgow.com/mcp*   (Caddy, bearer-token gate)
        ├── cms-mcp          :8002 → /mcp          (content MCP)
        └── cms-gateway-mcp  :8003 → /mcp/gateway  (gateway MCP)

EC2 (joellithgow-cms SSH alias) — fallback until retired; staging still lives here:
  ├── joellithgow-cms-prod-1, -mcp-1, -gateway-mcp-1 → old prod stack, stale data, no traffic
  ├── joellithgow-cms-staging-1     → port 8001 → cms-staging.joellithgow.com
  └── ~/backups/latest.db.gz        ← nightly cron at 2am UTC (backs up the stale EC2 DB)
```

## DB / backup flow

**This flow covers the EC2 fallback box, whose DB has been stale since the 2026-10-01 cutover.** Live prod is backed up by Litestream to R2 (see *Where prod runs*). To get live prod locally, `litestream restore` from the R2 replica (credentials in jlithgow-ops `litestream-secrets.enc.yaml`) until `make db-sync` is repointed.

The EC2 backup is a gzipped raw SQLite binary.

```
make backup           →  EC2: docker cp prod container → ~/backups/content-TIMESTAMP.db.gz
                                                       → symlink ~/backups/latest.db.gz

make db-sync          →  scp latest.db.gz from EC2
                      →  gunzip + docker cp into local cms-local container
                      →  restart cms-local

make staging-restore  →  ssh: restore-db.sh latest.db.gz → staging container
                      →  docker restart staging
```

**Note:** prod is the content home (holds the live docs); staging is currently empty/scratch. Use `make db-sync` (prod backup → local) to get real content locally — `make db-sync-staging` pulls staging, which is empty unless you repopulate it.

## Rebuilding the site

The Astro frontend is rebuilt on Netlify. **Publishing rebuilds the site automatically:** the prod CMS has an active webhook to the Netlify build hook (events `document.published`, `document.unpublished`, `document.deleted` and `changeset.published`), so a publish through the editor or the MCP tools updates the site in about a minute. `make webhooks` lists the registered webhooks. For a rebuild with no content change, use the **"Rebuild site"** button in the editor toolbar — prod only; it POSTs `/api/rebuild`, which fires the same build hook from `NETLIFY_BUILD_HOOK_URL`.

## Drafts on the blog index

The site is built from published documents only, so a post that was never published has no card on `/blog`. When you are logged in and press **Edit** there, the embed script draws a card for each unpublished `blog_post` and `definition` from the `<template data-cms-draft-template="…">` blocks in `src/pages/blog/[...page].astro` (latest quarter only, where a new post would land), and the picker next to Edit chooses which: live + all drafts (default), live only, or live + one open changeset. The contract and its limits live in the astraeus editor README (*Inline editing on a static site*). Things worth knowing:

- Only `blog_post` and `definition`. Gateway drafts (Spotify months, iNat outings held for review) are not drawn; edit those in the editor shell. A drawn definition is a read-only preview, like the live definition cards on the index; edit its term, definition and notes in the shell.
- The template is one post card without links, so a draft with `links` shows without them; open it in the shell for that.
- **New drafts collect in a "Staging" changeset** (`CMS(default_changeset=…)`, `CMS_DEFAULT_CHANGESET`, default `Staging`; empty turns it off; astraeus ADR 025). A post made with `create_document` (the MCP bot, hermes), or an edit that names no changeset, joins the open changeset called Staging, made if none is open; publishing it starts a fresh one next time. The toolbar's **Publish** ships the changeset you are working in, else Staging, and the confirm says which and how many documents (so on a phone: log in, Edit, check the posts, Publish). Staging is a bag: it ships everything in it, so move a half-written post to another changeset in the panel first. Gateway runs name their own changeset and are not affected. Publishing one document on its own (`publish_document`) takes it out of Staging.
- **Drafts made before Staging existed are in no changeset**, so publishing Staging skips them. Move them once with `uv run python -m cms.stage_orphan_drafts --cms-url https://cms.joellithgow.com` (a dry run that lists them; add `--api-key "$CMS_API_KEY" --apply` to move them). Safe to re-run. It changes no document.
- *All drafts* in the picker shows every unpublished post whether or not it is in a changeset, so it still finds strays.
- The picker lives in the embed bundle (`starlette_editor/static/embed.js`, built from astraeus `editor_src/embed/`). A change there reaches prod through the usual path: merge astraeus, then build a new image and bump the pin in jlithgow-ops (see *Where prod runs*). `build-cms.yml` builds against astraeus `main` whatever the `Dockerfile` says, but it only runs on a push touching `cms/**`, `Dockerfile`, `pyproject.toml` or the workflow (or a manual run), so a site-only merge builds nothing. Bumping the `Dockerfile`'s `ASTRAEUS_REF` is the usual way to trigger it, and keeps local `make dev` builds on the same astraeus.

## MCP servers

`starlette-cms` exposes MCP via a standalone `build_mcp_server()` FastMCP builder — it is **not**
mounted in-process on the CMS app. So MCP runs as its own process:

- **Local / Claude Code** — stdio launcher, no deploy needed:
  `uv run python -m cms.mcp_server` (defaults to `CMS_URL=https://cms.joellithgow.com`).
- **Remote (the hermes content bot, which runs in the cluster, and remote Claude)** — two HTTP Deployments in the cluster behind
  `mcp-proxy` (Caddy):
  - `cms-mcp` → `cms.joellithgow.com/mcp` — content CRUD/publish tools.
  - `cms-gateway-mcp` → `cms.joellithgow.com/mcp/gateway` — Spotify/iNat sync tools.
    (Caddy rewrites `/mcp/gateway` → `/mcp` since the MCP server serves at `/mcp`. It is not
    `/gateways`: the CMS serves the gateway admin UI there.)

Deploy: they run the same image as the CMS, so a tool change ships with the image-pin bump in
jlithgow-ops (see *Where prod runs*). The `mcp-deploy` / `caddy-deploy` targets only touch the EC2 fallback.

**Auth model** (important — the `/mcp` + `/mcp/gateway` routes are public):
- The CMS API uses `auth="apikey"` with `read_auth=False` — **writes need `CMS_API_KEY`; reads are
  intentionally public** (the static site needs them).
- The editor/gateway **shell pages are session-login gated** (`check_session_auth`, users from
  `CMS_ADMIN_USERS`) — they'd otherwise leak the api-key in their HTML. See `cms/main.py`.
- The MCP routes are **write-capable**, so `mcp-proxy` **bearer-token gates** `/mcp` + `/mcp/gateway`
  against `MCP_TOKEN`, held in jlithgow-ops `mcp-proxy-secrets.enc.yaml` (sops) — **never committed
  in plaintext**. The sidecars are ClusterIP-only and the tunnel sends only `/mcp*` to the proxy, so
  the gate can't be bypassed. Give the same token to hermes and any MCP client as
  `Authorization: Bearer <token>`. Keep the gate's `respond 401` inside the `route` block in the
  proxy's Caddyfile: outside it, Caddy runs `handle` first and the gate silently never runs.
- **OAuth, for the Claude app connector.** The mobile connector form has no bearer-token field, only
  OAuth client details, so `cms/oauth.py` is a small single-user OAuth server: one pre-registered
  client, the CMS login as the sign-in step (`/oauth/authorize` → `/api/auth/login`), and stateless
  HMAC-signed tokens (rotate `OAUTH_SIGNING_SECRET` to revoke everything). `mcp-proxy` calls
  `/oauth/verify` before proxying; the static bearer tokens still work for hermes. It needs
  `OAUTH_CLIENT_ID`, `OAUTH_CLIENT_SECRET`, `OAUTH_SIGNING_SECRET` and `CMS_SESSION_SECRET`; with any
  unset the routes aren't mounted. Tests: `uv run --with pytest --with pytest-asyncio --with respx python -m pytest tests/` (`python -m`, so `cms` is importable).

## Media library (images on posts)

`cms/media.py` mounts mediakit at `/media`, backed by the private R2 bucket `jlithgow-media`. It is only built when `MEDIA_BUCKET` is set, so local dev and staging run without it. Mediakit serves every image itself: `/media/iiif/<key>/<region>/<size>/0/default.webp` makes a derivative on first request and answers 302 to a presigned R2 URL (1 h). The bucket has no public access and its keys never leave the pod.

- **Env:** `MEDIA_BUCKET`, `MEDIA_ENDPOINT_URL` and `MEDIA_CATALOG_PATH` (default `./cms/data/media.db`, so it sits on the pod's volume) are plain env in the Deployment. `MEDIA_ACCESS_KEY_ID` / `MEDIA_SECRET_ACCESS_KEY` come from the `media` secret (jlithgow-ops `media-secrets.enc.yaml`, an R2 token scoped to `jlithgow-media` only, never the backups bucket's). Optional `MEDIA_API_KEY` for Bearer access. Rotating the token means re-encrypting that file and restarting `astraeus-cms`.
- **Renditions are public, originals are not.** `MEDIA_DERIVATIVES_BUCKET` (`jlithgow-media-public`) and `MEDIA_DERIVATIVES_PUBLIC_URL` (`https://media.joellithgow.com`) send IIIF renditions to a separate bucket on a Cloudflare custom domain (jlithgow-ops `r2.tf`), where the edge caches them (`Cache-Control` is set on write, 30 days). `/media/iiif/...` stays the stable URL in posts and 302s there. The R2 token must cover both buckets. Deleting an asset deletes its renditions; a CDN may still hold a cached copy until its TTL.
- **Auth:** reads (`GET /media/assets`, the IIIF routes) are public, because the static site's `<img>` tags carry no credentials. The admin pages (`/media/admin`, picker at `?picker=1`) and every write need the CMS login cookie or `Authorization: Bearer $MEDIA_API_KEY`. `/media/assets/<key>` hands a presigned URL for the *original* to anyone who knows a key.
- **Media MCP.** `cms/media_mcp_server.py` wraps mediakit's MCP server (list, search, get, upload from a public https URL, update, delete, IIIF URL) and runs as the `cms-media-mcp` sidecar on port 8004, reached at `/mcp/media` through mcp-proxy. It calls `/media` over HTTP with `MEDIA_API_KEY` as the Bearer key and hands back URLs under `MEDIA_PUBLIC_URL`, so the CMS pod and the sidecar read the same key from the `media` secret.
- **The catalog is state.** `media.db` holds alt text, tags and references; the bucket holds only the files. It needs Litestream (jlithgow-ops), or a pod move loses it.
- **Putting an image on a post.** The blog `image` field is a `JSONField` `{src, alt}`, not an `ImageField`, so the editor has no picker for it. Upload at `/media/admin/upload` (works from a phone), then set `image.src` to the **absolute** URL `https://cms.joellithgow.com/media/iiif/<asset key>/full/1200,/0/default.webp` and `image.alt`. Absolute because the site is static on Netlify: a relative `src` resolves against Netlify and 404s. `get_block_schema("blog_post")` documents the shape.
- **Browser uploads PUT straight to R2**, so the bucket needs a CORS rule allowing `PUT` from `https://cms.joellithgow.com` (Terraform, jlithgow-ops).

## Gateways (Spotify + iNaturalist sync)

Both gateways are `starlette-cms-gateways` subclasses in `cms/gateways/`, run through the `sync_gateway` MCP tool
(hermes, the Claude app), the admin page (`/gateways`) or `gateways sync`. They are configured per gateway, not hard-coded:

- **One post per outing / month.** iNat: same observed date and observations within **3 km** of each other
  (chained, so a trail is one outing; `DEFAULT_RADIUS_M = 3000`, override with `INATURALIST_OUTING_RADIUS_M`) is one
  outing, so two places in a day are two posts. On the real data only 2026-07-04 (Oakland, then Albany) splits; at
  1 km the 05-25 and 09-05 hikes split wrongly too. Everything that clusters (gateway, migration, report) reads
  `outing_radius_m()`. The first outing of a day keeps `inaturalist:outing:YYYY-MM-DD` / `nature-outing-YYYY-MM-DD`;
  later ones get `:2`, `-2`, and an outing keeps its document when observations are added (matched by observation
  id, not by order). Spotify: `spotify:dump:YYYY-MM`. `publish_date` is the observed date / the 1st of the month; the
  site sorts by it.
- **Owned vs. seeded fields.** `owned_fields` are machine-sourced and refreshed: iNat count, species, observations,
  photo URLs, bounding box **and `tags`** (so adding a bird to an outing later adds `birds`; a hand edit to an
  outing's tags is overwritten, an accepted trade-off); Spotify songs and song_count. Title, place and publish date
  (and Spotify's `tags`) are written once at creation, then belong to the editor. Only owned fields are hashed and
  written on update, so a re-sync that finds nothing new writes nothing and a hand edit to anything else is never
  overwritten. Stored iNat observations are slim records (`curate_observation`), not iNaturalist's raw payload, and
  carry no `quality_grade` (community IDs change it, which would republish a post for nothing the site shows).
- **A run is one changeset, published once at the end**: one `changeset.published` webhook, so one site build however
  many posts changed, and all or nothing (a quiet sync opens no changeset and starts no build). If the publish fails the
  run raises (the job is an error) and the cursor stays, so the next run finishes the posts. A post a person has a
  draft on, or one you unpublished, is left alone and named in the sync reply (the code calls it *deferred*). Nothing
  remembers it: an incremental run meets it again only if the source changes, so once they publish or discard the
  draft run `all_time` to catch it up. The gateway's own leftover draft (from a failed publish) is finished, not
  left alone, the next time a run meets the doc. See ADR 023 in astraeus.
- **Ranges and the cursor.** `since_last_sync` (default), `all_time` (first run / repair), `custom`
  (`from_date`..`to_date`, the observed date or liked month). The cursor and job history live **in the
  CMS's own database** (`content.db`, so Litestream carries them to R2); the MCP sidecar and `gateways sync` read and
  write them through the CMS gateway API (`/api/gateways/{name}/cursor|runs`), so the admin page, the MCP tool
  and the CLI share one cursor and it survives restarts. It moves to the run's start after any run that did not
  raise (never after `custom`); items left alone or failed never hold it back. The sidecar needs no volume and no
  `GATEWAY_JOBS_DB`.
- **Spotify floor.** `MONTH_FLOOR = "2025-01"`: `all_time` and `since_last_sync` never touch an earlier month. The 31
  pre-2025 monthly posts are deleted by the migration below. A `custom` range ignores the floor on purpose, so a
  backfill of older months re-creates them as new *published* posts.
- **Deletions.** An incremental run cannot see an unliked song or a deleted observation; only `all_time` can, and
  no sync ever deletes a document. A song you un-like drops out the next time its month is refreshed.
- **Check the clustering radius on real data:** `uv run python -m cms.inat_outing_report --only-differing`.
- **One-off migration of existing docs** (`cms/migrate_gateway_docs.py`; read its docstring): `report` (read-only:
  pending drafts told apart as gateway-only vs a person's, the pre-floor Spotify months, what an `all_time` sync would
  create and update, the stale open changesets old runs left) → `apply` (discards gateway-only drafts; with
  `--delete-pre-floor --snapshot-confirmed` also deletes the pre-floor months; then cleans the stale changesets:
  deletes those holding only gateway docs, trims gateway docs out of mixed ones, leaves any holding a person's draft
  and any `review`/`scheduled` one; `--allow-prod` against prod. Re-run it once a person has resolved their draft) → one
  `PYTHONPATH=. uv run gateways sync <name> --range all_time` per gateway (or the `sync_gateway` MCP tool) → `verify` (no drafts, no leftover open changeset, no stray `draft` key or raw payload,
  slugs and publish_dates unchanged except on 07-04, and a second `all_time` sync writes nothing). Develop against a
  local `litestream restore` of prod (credentials in jlithgow-ops `litestream-secrets.enc.yaml`), never prod first,
  and show Joel the report before anything is written. Titles are seeded, so check the 07-04 posts' titles by hand:
  the document that kept the old ref keeps its old title even if its cluster is the Albany one. Publishing rebuilds the
  site through the Netlify webhook (see *Rebuilding the site*), so there is no Rebuild to press afterwards. Each gateway's
  sync is one publish (one build), but each deleted pre-floor month fires its own `document.deleted` (one build each):
  the report's section 5 counts them. The CMS can't pause a webhook (only create/delete) and doesn't coalesce, so
  decide beforehand whether to accept those builds or delete the hook for the `apply` and re-create it.

## Environment

`.env` controls which CMS Astro talks to. Defaults to local:

```
ASTRAEUS_URL=http://localhost:8001   # local dev default
ASTRAEUS_API_KEY=local-secret        # matches docker-compose.local.yml default
```

Prod/staging keys are commented out in `.env` — uncomment to point Astro at them.

## Project layout (key files)

```
Makefile                        # all dev/ops commands — start here
docker-compose.local.yml        # local CMS only (cms-local on :8001)
docker-compose.yml              # EC2 fallback: cms-prod (:8000) + cms-staging (:8001) + MCP sidecars (:8002/:8003)
Dockerfile                      # CMS image — multi-stage, clones astraeus at build (ASTRAEUS_REF)
Caddyfile                       # EC2 fallback reverse proxy; the live one is jlithgow-ops mcp-proxy
scripts/
  backup-prod-db.sh             # runs on EC2 (fallback) — backs up its prod container → ~/backups/
  restore-db.sh                 # runs on EC2 — restores .db.gz into a container
cms/
  main.py                       # CMS app entrypoint
  schema.py                     # document type definitions
  seed.py                       # one-time seed (MD/MDX → CMS API)
  oauth.py                      # OAuth server for the MCP connectors (Claude app)
  mcp_server.py                 # content MCP (stdio local / streamable-http sidecar :8002)
  gateway_mcp_server.py         # gateway MCP sidecar (:8003) — sync tools
  gateways/                     # Spotify + iNaturalist sync workers
src/lib/
  astraeus-loader.ts            # paginated HTTP loader for Astro Content Layer
  astraeus-types.ts             # TypeScript interfaces mirroring CMS schemas
  astraeus.ts                   # public API shim (delegates to getCollection)
src/content/config.ts           # collection definitions + Zod schemas
```

## Content collections

| Collection | CMS doc type | Notes |
|---|---|---|
| `blog` | `blog_post` | Every post (one type: articles, short thoughts, link lists) |
| `projects` | `project_page` | Case studies |
| `experience` | `experience_entry` | Work history |
| `spotifyDumps` | `spotify_liked_dump` | Gateway-sourced |
| `inaturalistOutings` | `inaturalist_outing` | Gateway-sourced |
| `definitions` | `definition` | Glossary terms; routes under `/blog/dictionary/`. CMS-only, not seeded from local files |
| `applications` | Local MDX | Not in CMS — intentional |

### Writing content

**`cms/schema.py` is the source of truth for field names and types.** `src/lib/astraeus-types.ts` and `src/content/config.ts` mirror it by hand, so check `schema.py` first if they disagree. For voice, how a post shows on the feed and tag habits, see `docs/blog-conventions.md`.

- CMS fields are `snake_case` (`publish_date`, `reading_time`). The legacy markdown files in `src/content/blog/` are seed sources with `camelCase` frontmatter (`publishDate`, `readingTime`); their `type` and `hasDetailPage` are no longer read. The site reads from the CMS, not from those files.
- A definition is **not** a blog post: it has `term`, `definition`, `personal_notes`, `sources`, `tags` and no `kind` of its own. The site tags each feed item with a synthetic `kind` (`post`, `spotify_dump`, `nature_outing`, `definition`) when it merges them into blog feeds (`src/lib/astraeus.ts`). `blog_post` has no type field: every post has a detail page, and the feed card shows the excerpt, or the whole body when it is short (see `docs/blog-conventions.md`).
- Publishing, unpublishing and deleting fire the Netlify build hook through a CMS webhook. See *Rebuilding the site*.

## Gotchas

- The `gateways` console script cannot import `cms` on its own (the repo root is not on its path): run it as
  `PYTHONPATH=. uv run gateways ...`, from the repo root. `python -m` entry points (`cms.mcp_server`, `cms.migrate_gateway_docs`) are fine.
- The Docker **image** no longer needs a sibling astraeus checkout — the multi-stage Dockerfile clones astraeus (pinned by `ASTRAEUS_REF`) at build time. **Local dev** (`docker-compose.local.yml`) still bind-mounts `../astraeus/`, so the sibling checkout is still required for `make dev`.
- `bun run dev` hangs if the CMS is unreachable — always run `make cms-up` or `make dev` instead of bare `bun run dev`.
- The content loader returns `[]` silently if `ASTRAEUS_API_KEY` is unset — useful for skipping CMS during pure frontend work.
- Docker Desktop on Mac: volume paths (`/var/lib/docker/volumes/...`) are inside a VM — always use `docker cp` to move files in/out of containers, never direct volume path access.
