"""
MCP server for gateway management — trigger syncs, list gateways, view results.

Runs alongside the content MCP server as the cms-gateway-mcp sidecar on port
8003 (streamable-http), fronted by nginx /mcp/gateway. Exposes the Spotify /
iNaturalist gateway sync tools to remote clients (the hermes content bot).

Usage:
    uv run python -m cms.gateway_mcp_server --transport streamable-http --port 8003
"""
from __future__ import annotations

import argparse
import json
import os

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from starlette_cms.mcp.server import (
    DEFAULT_LIST_LIMIT,
    MAX_LIST_LIMIT,
    MAX_RESPONSE_CHARS,
    READ_ONLY,
    _reject_unknown_arguments,
    summarize_document,
)
from starlette_cms_gateways.base import SyncRange
from starlette_cms_gateways.client import CMSClient, CMSError
from starlette_cms_gateways.discovery import discover_gateways
from starlette_cms_gateways.runner import run_recorded
from starlette_cms_gateways.state import RemoteSyncState

CMS_URL = os.environ.get("CMS_URL", "http://cms-prod:8000")
CMS_API_KEY = os.environ.get("CMS_API_KEY", "")
# This sidecar keeps no state of its own. The sync cursor and the
# job history live in the CMS's database and are read and written through the CMS
# gateway API (RemoteSyncState), so a run from here, from the admin page or from
# the `gateways` CLI all share one cursor, and it survives a restart of this pod.

mcp = FastMCP("joellithgow-gateways")

# Follows the astraeus MCP tool conventions (ADR 005): list tools return bounded summaries,
# long lists are capped, unknown arguments fail loudly, every tool is annotated.
# A sync writes and publishes (which fires the site-rebuild webhook) and reads Spotify /
# iNaturalist, so it is open-world; it is idempotent and destroys nothing.
SYNC = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
)
# How many deferred / failed items a sync reply names before saying "and N more".
MAX_NAMED = 10


def _get_client() -> CMSClient:
    return CMSClient(base_url=CMS_URL.rstrip("/"), api_key=CMS_API_KEY or None)


async def _sync_gateway_inner(
    gateway_name: str,
    client: CMSClient,
    sync_range: SyncRange | None = None,
) -> dict:
    """Run a single gateway sync, return result summary."""
    gateways = discover_gateways()
    if gateway_name not in gateways:
        available = ", ".join(sorted(gateways)) or "(none)"
        return {"error": f"Unknown gateway {gateway_name!r}. Available: {available}"}

    gateway_cls = gateways[gateway_name]
    try:
        state = RemoteSyncState(client)
        gateway = gateway_cls(cms_client=client, job_store=state, job_store_key=gateway_name)
        result = await run_recorded(gateway, state, gateway_name, sync_range)
        return {
            "gateway": gateway_name,
            "range": result.window.to_dict() if result.window else {"mode": gateway.range.mode},
            "created": result.created,
            "updated": result.updated,
            "skipped": result.skipped,
            "deferred": result.deferred,
            "errors": len(result.errors),
            "error_details": [
                {"import_ref": ref, "message": msg} for ref, msg in (result.errors or [])
            ],
        }
    except CMSError as exc:
        return {"gateway": gateway_name, "error": str(exc)}


@mcp.tool(annotations=READ_ONLY)
async def list_gateways() -> str:
    """List all available gateway implementations and their metadata."""
    gateways = discover_gateways()
    if not gateways:
        return "No gateways found."

    lines = ["## Available Gateways\n"]
    for name, cls in sorted(gateways.items()):
        svc = getattr(cls, "service_name", "?")
        bt = getattr(cls, "block_type", "?")
        auto = getattr(cls, "auto_publish", False)
        imm = getattr(cls, "immutable", False)
        default_range = getattr(cls, "default_range", "since_last_sync")
        summary = " ".join((cls.__doc__ or "").split("\n\n")[0].split())
        lines.append(
            f"- **{name}**  "
            f"service=`{svc}`  block=`{bt}`  "
            f"auto_publish={auto}  immutable={imm}  default_range={default_range}"
            + (f"\n  {summary}" if summary else "")
        )
    return "\n".join(lines)


def _named(items: list[str]) -> list[str]:
    """Bullet lines for *items*, at most MAX_NAMED, then how many more there are."""
    lines = [f"    - {item}" for item in items[:MAX_NAMED]]
    if len(items) > MAX_NAMED:
        lines.append(f"    - …and {len(items) - MAX_NAMED} more")
    return lines


@mcp.tool(annotations=SYNC)
async def sync_gateway(
    gateway_name: str,
    range: str | None = None,  # noqa: A002
    from_date: str | None = None,
    to_date: str | None = None,
) -> str:
    """
    Sync a named gateway into the CMS.

    Discovers external service data (Spotify liked songs, iNaturalist
    observations) and upserts it as data documents, one per song or sighting,
    publishing the whole run as one changeset (so one site rebuild, and all or
    nothing). A re-sync that finds nothing new changes nothing. Every field of a data
    document belongs to the gateway, so a re-sync overwrites a hand edit. Call
    ``list_gateways`` first to see available gateway names.

    Args:
        gateway_name: The entry-point name of the gateway, e.g.
            ``spotify-likes`` or ``inaturalist-observations`` (one data document
            per song or sighting).
        range: What to cover. ``since_last_sync`` (the default for both
            gateways) fetches only what changed since the last run;
            ``all_time`` re-reads everything (first run, or to repair);
            ``custom`` backfills ``from_date``..``to_date``.
        from_date: ``YYYY-MM-DD``. For ``custom``: the first observed date
            (iNaturalist) or liked date (Spotify) to include.
        to_date: ``YYYY-MM-DD``. For ``custom``: the last one to include.
    """
    try:
        sync_range = (
            SyncRange.parse(range, from_date, to_date) if (range or from_date or to_date) else None
        )
    except ValueError as exc:
        return f"❌ Invalid range: {exc}"

    client = _get_client()
    try:
        result = await _sync_gateway_inner(gateway_name, client, sync_range)
    finally:
        await client.close()

    if "error" in result:
        return f"❌ {result['error']}"

    mode = result["range"]["mode"]
    note = " (no cursor yet, so everything)" if result["range"].get("fell_back") else ""
    parts = [
        f"✅ Synced **{result['gateway']}** — {mode}{note}",
        f"  • Created: {result['created']}",
        f"  • Updated: {result['updated']}",
        f"  • Skipped (nothing new): {result['skipped']}",
        f"  • Errors: {result['errors']}",
    ]
    if result["deferred"]:
        parts.append(
            f"  • Left alone: {len(result['deferred'])} — someone has an unpublished draft on "
            "these. Publish or discard the draft, then run `all_time` to catch them up:"
        )
        parts += _named([f"`{ref}`" for ref in result["deferred"]])
    if result.get("error_details"):
        parts.append("    (an `all_time` run tries these again)")
        parts += _named([f"`{e['import_ref']}`: {e['message']}" for e in result["error_details"]])

    return "\n".join(parts)


@mcp.tool(annotations=READ_ONLY)
async def get_recent_gateway_items(
    block_type: str,
    limit: int = DEFAULT_LIST_LIMIT,
) -> str:
    """
    List recently synced CMS documents for a gateway, as short summaries WITHOUT bodies.

    Each item has its id, type, slug, title, status, dates and an excerpt. To read one in
    full, use the content MCP's get_document tool with its id. Output is capped at
    20,000 characters.

    Args:
        block_type: The CMS document type for this gateway, e.g.
            ``spotify_like`` or ``inat_observation``.
        limit: Maximum number of documents to return, 1-50. Default 10.
    """
    if not 1 <= limit <= MAX_LIST_LIMIT:
        return f"❌ limit must be between 1 and {MAX_LIST_LIMIT}, not {limit}."
    client = _get_client()
    try:
        data = await client.list_documents(doc_type=block_type, limit=limit)
    finally:
        await client.close()

    docs = [summarize_document(d).model_dump() for d in data.get("documents", [])]
    total = data.get("total", 0)
    text = (
        f"{total} document(s) of type {block_type!r} "
        f"(showing {len(docs)}):\n" + json.dumps(docs, indent=2, default=str)
    )
    if len(text) > MAX_RESPONSE_CHARS:
        text = (
            text[:MAX_RESPONSE_CHARS]
            + f"\n… truncated at {MAX_RESPONSE_CHARS} characters; pass a smaller `limit`."
        )
    return text


_reject_unknown_arguments(mcp)  # a misnamed argument must fail, not be silently dropped


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="joellithgow gateway MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="streamable-http",
        help="MCP transport (default: streamable-http)",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Bind address for HTTP transports",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8003,
        help="HTTP port for SSE/streamable-http transports (default: 8003)",
    )
    args = parser.parse_args()

    if args.transport in ("sse", "streamable-http"):
        mcp.settings.host = args.host
        mcp.settings.port = args.port

    mcp.run(transport=args.transport)
