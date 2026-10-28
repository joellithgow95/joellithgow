# joellithgow.com

Personal portfolio site for Joel Lithgow — creative technologist. Built with Astro (SSG) and backed by a self-hosted headless CMS called [Astraeus](https://github.com/ASneakyToast/astraeus).

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│  Netlify (static hosting)                        │
│  Astro SSG — fetches content from CMS at build   │
└────────────────────┬────────────────────────────┘
                     │ ASTRAEUS_URL (build-time)
         ┌───────────┼───────────────┐
         ▼           ▼               ▼
    localhost:8001  cms-staging     cms-prod
    (local dev)     .joellithgow    .joellithgow
                    .com            .com
                    (EC2 :8001)     (k3s cluster)
```

- **Frontend**: Astro 5 static site, deployed to Netlify. Fetches all content via Astro Content Layer loaders at build time — no client-side CMS calls.
- **CMS**: Astraeus (starlette-cms) running on a k3s cluster (managed in `jlithgow-ops`; staging is still on EC2). Exposes a REST API. Content is published manually via the editor UI or gateway syncs.
- **Gateways**: Python workers that pull external data (Spotify liked tracks, iNaturalist observations) into the CMS as draft documents for review and publish.
- **Backups**: Prod DB is streamed continuously to Cloudflare R2 by Litestream (`jlithgow-ops-backups`, `astraeus-cms/prod/content.db`) and restored on pod start. The nightly EC2 backup below covers the fallback box only and is stale.

---

## Project structure

```
joellithgow/
├── cms/                        # Astraeus CMS backend
│   ├── main.py                 # App entrypoint — wires CMS + editor + gateways
│   ├── schema.py               # Document type definitions
│   ├── seed.py                 # One-time seed script (MD/MDX → CMS API)
│   ├── mcp_server.py           # MCP server for Claude Code sessions
│   └── gateways/               # External data sync workers
│       ├── spotify_likes.py
│       └── inaturalist_observations.py
├── nginx/
│   └── cms.conf                # Nginx reverse proxy for prod + staging subdomains
├── scripts/
│   ├── backup-prod-db.sh       # EC2: backup prod container → ~/backups/latest.db.gz
│   ├── restore-db.sh           # EC2: restore .db.gz into a named container
│   ├── backfill_slugs.py       # One-time migration helper
│   └── sync-starlette-cms.sh   # Dev helper: sync local astraeus package
├── src/
│   ├── content/
│   │   ├── config.ts           # Astro Content Layer collection definitions + Zod schemas
│   │   └── applications/       # Local MDX (not in CMS — intentional)
│   ├── lib/
│   │   ├── astraeus-loader.ts  # Raw HTTP loader (paginated) for Content Layer
│   │   ├── astraeus-types.ts   # TypeScript interfaces mirroring CMS schemas
│   │   └── astraeus.ts         # Public API shim — delegates to getCollection()
│   ├── components/             # Astro UI components
│   ├── pages/                  # Route pages
│   └── styles/                 # Global CSS + theme system
├── Dockerfile                  # CMS image (uv + starlette-cms workspace)
├── docker-compose.yml          # EC2: cms-prod (:8000) + cms-staging (:8001)
├── docker-compose.local.yml    # Local dev: cms-local (:8001)
├── Makefile                    # Dev/ops commands — start here
├── piccolo_conf.py             # Piccolo ORM config for SQLite migrations
├── pyproject.toml              # Python project manifest (uv workspace member)
└── .env.example                # Required environment variables
```

---

## Local development

### Prerequisites

- [Bun](https://bun.sh)
- Docker and Docker Compose
- The [astraeus](https://github.com/ASneakyToast/astraeus) monorepo checked out as a sibling directory (`../astraeus/`)

### First-time setup

```bash
cp .env.example .env
# .env is pre-configured for local dev — no changes needed to get started

bun install
```

### Daily workflow

```bash
make db-sync   # pull latest prod backup from EC2 → restore local CMS
               # skip this if you synced recently and don't need fresh content

make dev       # start local CMS (http://localhost:8001) + Astro HMR (http://localhost:4321)
```

`make dev` starts both processes together. If you want them separately:

```bash
make cms-up    # CMS only
bun run dev    # Astro only (requires cms-up to be running)
```

### Rebuild the CMS image

Only needed after changes to `Dockerfile`, `cms/`, or the astraeus packages:

```bash
make cms-build
```

---

## Content collections

| Collection | Source | Notes |
|---|---|---|
| `blog` | Astraeus CMS (`blog_post`) | Articles, thoughts, link collections |
| `projects` | Astraeus CMS (`project_page`) | Case studies with body blocks |
| `experience` | Astraeus CMS (`experience_entry`) | Work history |
| `applications` | Local MDX (`src/content/applications/`) | Not in CMS — intentional |

Gateway-sourced content (Spotify dumps, iNat outings) lives in the CMS as additional document types and is merged into the blog index at build time.

---

## Operations

All ops commands run through `make`. Run `make help` to see the full list.

### Backups

Live prod is backed up by Litestream to R2; nothing to run. The commands below act on the **EC2 fallback box**, whose data has been stale since the 2026-10-01 cutover to the cluster. To restore live prod locally, `litestream restore` from the R2 replica until `make db-sync` is repointed.

The EC2 box is backed up nightly at 2am UTC to `~/backups/` as `latest.db.gz` (gzipped SQLite binary). To trigger a backup immediately:

```bash
make backup
```

### Syncing environments

```bash
make db-sync          # EC2 latest backup → local dev container (stale: not live prod)
make staging-restore  # EC2 latest backup → staging container + restart (stale)
make staging-restart  # restart staging container only (no DB change)
```

These do not sync with live prod; see *Backups*.

### SSH access

```bash
make ssh   # → ssh joellithgow-cms
```

### Cron (already installed)

Nightly backup cron is installed on EC2 (`crontab -l` to verify). To reinstall: `make cron-install`.

---

## Deployment

Prod CMS runs on the k3s cluster, declared in `jlithgow-ops` (`cluster/apps/astraeus-cms/`). To deploy a CMS change, merge to `main` here (CI builds the image and tags it `jl-<sha>-astraeus-<sha>`), then bump the image pin in jlithgow-ops to that tag and merge. Secret changes need `kubectl -n astraeus rollout restart deploy/astraeus-cms`. EC2 is a fallback until retired and still hosts staging; see `CLAUDE.md` for details.

The Astro frontend deploys automatically to Netlify on push to `main`. The build requires `ASTRAEUS_URL` and `ASTRAEUS_API_KEY` set in Netlify environment variables.

---

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | Astro 5, TypeScript, CSS custom properties |
| CMS | Astraeus (starlette-cms), Python 3.12, SQLite |
| Package manager (Python) | uv |
| Infrastructure | Docker, Nginx, EC2 |
| Hosting | Netlify (frontend), EC2 (CMS) |
| Gateways | Spotipy (Spotify), iNaturalist v1 API |

---

**Joel Lithgow** — [joellithgow.com](https://joellithgow.com) · [LinkedIn](https://linkedin.com/in/joellithgow) · [GitHub](https://github.com/ASneakyToast)
