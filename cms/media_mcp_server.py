"""
MCP server entrypoint for the media library (astraeus-media mounted at /media).

Wraps astraeus-media's MCP server (list, search, get, upload, update, delete, IIIF URLs —
thin HTTP wrappers around the /media API) and serves it over a chosen transport.
Uploads go through upload_asset, which fetches a public https URL (SSRF-guarded in astraeus-media).

  - stdio (default) — local Claude Code / testing:
        MEDIA_API_KEY=... uv run python -m cms.media_mcp_server
  - streamable-http — remote clients via the cms-media-mcp sidecar, fronted by
    mcp-proxy at /mcp/media:
        uv run python -m cms.media_mcp_server --transport streamable-http --port 8004

The sidecar sets MEDIA_URL to the in-cluster service, so the image URLs the
tools return use MEDIA_PUBLIC_URL instead: posts are rendered on Netlify and
need the absolute public address. Both default to the public URL so local stdio
use works out of the box. MEDIA_API_KEY is the same key the CMS accepts as
``Authorization: Bearer`` on /media (cms/media.py).
"""
import argparse
import os

from astraeus_media.mcp.server import build_mcp_server

PUBLIC_URL = "https://cms.joellithgow.com/media"

MEDIA_URL = os.environ.get("MEDIA_URL", PUBLIC_URL)
MEDIA_PUBLIC_URL = os.environ.get("MEDIA_PUBLIC_URL", PUBLIC_URL)
MEDIA_API_KEY = os.environ.get("MEDIA_API_KEY")

mcp = build_mcp_server(
    base_url=MEDIA_URL.rstrip("/"),
    api_key=MEDIA_API_KEY,
    public_url=MEDIA_PUBLIC_URL,
)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="joellithgow media MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="MCP transport (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Bind address for HTTP transports",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8004,
        help="HTTP port for SSE/streamable-http transports (default: 8004)",
    )
    args = parser.parse_args()

    if args.transport in ("sse", "streamable-http"):
        mcp.settings.host = args.host
        mcp.settings.port = args.port

    mcp.run(transport=args.transport)
