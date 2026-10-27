# Self-contained multi-stage build.
#
# The builder clones the astraeus workspace (public repo) so the project's
# ../astraeus/... path dependencies resolve without needing a sibling checkout
# on the build host — the old requirement this replaces. The runtime stage
# keeps only the synced venv + workspace, dropping git and apt artifacts.

FROM python:3.12-slim AS builder

# uv + git (git only needed to clone the astraeus workspace)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Pin the astraeus workspace to a commit for reproducible builds. Bump
# ASTRAEUS_REF (or pass --build-arg ASTRAEUS_REF=<sha|tag>) to pull newer
# astraeus package changes.
ARG ASTRAEUS_REF=e49b18cf6943c6f8a6998d6ff75c60374be55ec8
RUN git clone https://github.com/joellithgow95/astraeus.git \
    && git -C astraeus checkout "${ASTRAEUS_REF}"

# Sync deps against the pinned workspace (../astraeus/... sources resolve here).
# `uv pip install -e .` registers the project's own entry points (gateways).
COPY joellithgow/pyproject.toml joellithgow/uv.lock* ./joellithgow/
WORKDIR /app/joellithgow
RUN uv sync --no-dev && uv pip install -e .

# ── Runtime stage ───────────────────────────────────────────────────────────
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Bring over the synced venv + the astraeus workspace it references.
COPY --from=builder /app/astraeus ./astraeus
COPY --from=builder /app/joellithgow/.venv ./joellithgow/.venv
COPY --from=builder /app/joellithgow/pyproject.toml /app/joellithgow/uv.lock* ./joellithgow/

# CMS source + piccolo config (bind-mounted over in compose; baked for standalone runs).
COPY joellithgow/cms/ ./joellithgow/cms/
COPY joellithgow/piccolo_conf.py ./joellithgow/piccolo_conf.py
RUN mkdir -p /app/joellithgow/cms/data

WORKDIR /app/joellithgow
ENV PATH="/app/joellithgow/.venv/bin:$PATH"

# Record which astraeus commit this image was built from. ARG has to be
# re-declared here — the builder-stage one is not in scope in this stage.
# Baked in rather than read from .git because the runtime image has no git,
# and a registry-pulled image may carry no .git at all.
ARG ASTRAEUS_REF
ENV ASTRAEUS_REF=${ASTRAEUS_REF}
LABEL org.opencontainers.image.revision=${ASTRAEUS_REF}
LABEL org.opencontainers.image.source=https://github.com/joellithgow95/joellithgow

EXPOSE 8000

# --no-sync: use the copied venv, never re-resolve at runtime (no uv cache in
# this stage). No --reload: the WatchFiles reloader looped as uv re-synced.
CMD ["uv", "run", "--no-sync", "uvicorn", "cms.main:app", "--host", "0.0.0.0", "--port", "8000"]
