# Astraeus CMS

Headless CMS backend for [joellithgow.com](https://joellithgow.com). Built with Astraeus — a lightweight, self-hosted CMS that exposes content over a REST API consumed by the Astro frontend at build time.

---

## What this is

This directory contains the Astraeus CMS backend that serves as the content source for the portfolio site. The Astro frontend fetches content from the CMS API during the Netlify build, replacing the previous file-based Astro content collections for projects, blog posts, and experience entries.

The `applications` collection remains file-based (local MDX) and is not served through Astraeus.

---

## Local development

> **TL;DR** — use `make dev` from the repo root. See the root `README.md` for the full workflow.

### Prerequisites

- Docker and Docker Compose
- [Bun](https://bun.sh) (for the Astro frontend)

### Start everything

```bash
# From repo root:
make db-sync   # pull latest prod backup from EC2 (first time / when you need fresh content)
make dev       # start local CMS + Astro HMR
```

The local CMS runs at `http://localhost:8001` via `docker-compose.local.yml`.  
The Astro frontend at `http://localhost:4321` points at it automatically.

### Environment variables

Copy `.env.example` to `.env` from the repo root — it's pre-configured for local dev:

```bash
cp .env.example .env
```

Key variables:

| Variable | Description |
|---|---|
| `ASTRAEUS_URL` | CMS URL for the Astro frontend. Default: `http://localhost:8001` |
| `ASTRAEUS_API_KEY` | API key for the Astro frontend. Default: `local-secret` |
| `CMS_LOCAL_API_KEY` | API key for `cms-local` container. Default: `local-secret` |
| `CMS_API_KEY` | API key for the EC2 prod instance (`cms-prod`) |
| `CMS_STAGING_API_KEY` | API key for the EC2 staging instance (`cms-staging`) |
| `DATABASE_URL` | SQLite path — set automatically by Docker Compose |

---

## Seeding content

The seed script reads content from the `data/` directory and pushes it to the CMS API.

### Seed all collections

```bash
cd cms
python seed.py
```

### Seed a specific collection

```bash
python seed.py --collection projects
python seed.py --collection blog
python seed.py --collection experience
```

### Data directory layout

```
cms/data/
  projects/      # One JSON file per project
  blog/          # One JSON file per post
  experience/    # One JSON file per role
```

Each file maps directly to the schema defined in `schema.py`.

---

## Verifying seeded documents

After seeding, confirm documents are present via the API:

```bash
# List all projects
curl -s http://localhost:8001/api/projects \
  -H "Authorization: Bearer $ASTRAEUS_API_KEY" | jq '.data | length'

# Fetch a single document by slug
curl -s http://localhost:8001/api/projects/your-slug \
  -H "Authorization: Bearer $ASTRAEUS_API_KEY" | jq .

# List blog posts
curl -s http://localhost:8001/api/blog \
  -H "Authorization: Bearer $ASTRAEUS_API_KEY" | jq '.[].title'

# List experience entries
curl -s http://localhost:8001/api/experience \
  -H "Authorization: Bearer $ASTRAEUS_API_KEY" | jq '.[].company'
```

---

## Netlify webhook

To trigger a Netlify build when content is published in Astraeus:

1. In the Netlify dashboard, go to **Site settings > Build & deploy > Build hooks** and create a new hook. Copy the webhook URL.

2. In the Astraeus admin panel (or via API), register the webhook:

```bash
curl -X POST http://localhost:8001/api/webhooks \
  -H "Authorization: Bearer $ASTRAEUS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://api.netlify.com/build_hooks/YOUR_HOOK_ID",
    "events": ["document.published", "document.unpublished", "document.deleted", "changeset.published"]
  }'
```

Event names are **past tense** and matched exactly, so `document.publish`
subscribes to nothing and the webhook lists as active while never firing. The
full set: `document.created`, `document.updated`, `document.published`,
`document.unpublished`, `document.deleted`, `changeset.published`.

**`changeset.published` matters.** Publishing a changeset fires it *once*
instead of one `document.published` per member document (ADR 018 §4). A webhook
subscribed only to document events therefore never rebuilds the site after a
changeset publish — which is what the editor's Review & Publish flow and the AI
chat both use.

3. Publish a document to confirm the hook fires and a Netlify build is triggered.

The build environment variables `ASTRAEUS_URL` and `ASTRAEUS_API_KEY` must be set in the Netlify dashboard under **Site settings > Environment variables** — they are intentionally omitted from `netlify.toml` to avoid committing credentials.

---

## Production deployment

The CMS runs on EC2 behind Nginx. Both `cms-prod` (port 8000) and `cms-staging` (port 8001) are defined in `docker-compose.yml` and managed via `make` from the repo root.

```bash
make ssh              # SSH into EC2
make staging-restart  # restart staging container
make staging-restore  # restore latest backup into staging
make backup           # trigger a prod backup right now
```

Caddy fronts the box and owns SSL (`Caddyfile`, deployed with `make caddy-deploy`).

The instance has ~908MB of RAM and a 1GB swapfile at `/swapfile`, persisted in
`/etc/fstab`. The swap was added when image builds still ran on the box and
BuildKit was being OOM-killed; builds moved to CI, so it is now insurance
rather than a requirement.

### Deploying astraeus changes

The image builds against a pinned astraeus commit (`ASTRAEUS_REF` in the
Dockerfile). **Restarting a container does not pick up astraeus changes** — it
reuses the image that was already built. Only a rebuild against a new ref does.

```bash
make deployed-ref     # which astraeus commit each container is running, vs latest main
make staging-deploy   # build in CI + deploy to staging  (:8001)
make prod-deploy      # build in CI + deploy to prod     (:8000)
make prod-deploy-nobuild   # deploy the current image without rebuilding
make staging-stop     # stop staging once you are done smoke-testing
```

**The image is built in CI, not on the box.** The instance has 908MB of RAM, and
building there ran `git clone` plus a full `uv sync` resolution alongside the
running services — BuildKit was OOM-killed repeatedly, surfacing as
`grpc: the client connection is closing`. A 1GB swapfile made it survivable, but
CI makes it free.

`make staging-deploy` resolves astraeus `main` to a SHA, triggers
`.github/workflows/build-cms.yml` with it, waits for the run, then pulls and
restarts on the box. One image serves all four services — they differ only by
command.

The ref has to be a SHA rather than the branch name. Docker keys its layer cache
on the literal command text, so `git checkout "main"` never changes: the clone
layer is reused indefinitely and the build succeeds while silently shipping
whatever `main` pointed at the first time it ran.

The astraeus commit is baked into the image as `ASTRAEUS_REF`, which is what
`deployed-ref` reads — the runtime image has no git, and a pulled image has no
`.git` directory.

Staging is not meant to run continuously. Bring it up to smoke-test a risky
deploy — migrations, new env vars, anything touching startup — then
`make staging-stop`. Drafts and changesets already cover content risk, and the
local instance covers code; staging only covers *deployment*.

Nightly prod backups run at 2am UTC via cron (`make cron-install` to set up).

---

## Gateways

Gateways are Python workers that pull external data into the CMS as published data documents, one changeset per run. In prod the `gateways-daily` CronJob (jlithgow-ops) runs them every morning.

### Available gateways

| Gateway | Document type | Source |
|---|---|---|
| `SpotifyLikesGateway` (`spotify-likes`) | `spotify_like` | One data document per liked track (ADR 026) |
| `INaturalistObservationsGateway` (`inaturalist-observations`) | `inat_observation` | One data document per observation (ADR 026) |

### Running a sync

1. Start the local CMS: `make cms-up` (from repo root)
2. Open the gateway admin UI: `http://localhost:8001/gateways/shell`
3. Click **Sync now** for the gateway you want to run
4. The run publishes what it found as one changeset; the documents are under **Data** at `http://localhost:8001/editor`

### Environment variables

| Variable | Description |
|---|---|
| `SPOTIPY_CLIENT_ID` | Spotify app client ID |
| `SPOTIPY_CLIENT_SECRET` | Spotify app client secret |
| `SPOTIPY_REFRESH_TOKEN` | OAuth refresh token — run `cms/gateways/get_spotify_token.py` once to obtain |
| `INATURALIST_USERNAME` | iNaturalist username to sync (defaults to `joel583`) |

### Adding a new gateway

1. Create a new file in `cms/gateways/` subclassing `BaseGateway`
2. Register it as an entry point in `pyproject.toml` under `[project.entry-points."starlette_cms_gateways.gateways"]`
3. Register the document type in `cms/schema.py`
4. Add Zod schema + Content Layer loader in `src/content/config.ts`
5. Add TypeScript interfaces in `src/lib/astraeus-types.ts`
6. Add fetch helper in `src/lib/astraeus.ts`

---

## Known gaps / future work

| Gap | Phase | Notes |
|---|---|---|
| MCP server integration | Phase 5 | Astraeus MCP server is not yet wired up; Claude Code cannot query CMS content directly during sessions |
| `DateField` serialization | - | Astraeus returns dates as ISO strings; the Astro integration layer must parse them back to `Date` objects to satisfy Zod schemas |
| Media kit / asset management | - | Binary assets (images, videos) are not yet stored in Astraeus; they remain in `public/` in the Astro repo |
| `applications` collection | - | Intentionally kept as local MDX; not migrated to Astraeus |
