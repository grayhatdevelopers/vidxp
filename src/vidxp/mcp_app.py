from __future__ import annotations

from functools import lru_cache
from importlib.resources import files


MCP_APP_RESOURCE_URI = "ui://vidxp/evidence-review-v1.html"
MCP_APP_MIME_TYPE = "text/html;profile=mcp-app"


@lru_cache(maxsize=1)
def load_mcp_app_html() -> str:
    """Load the self-contained MCP App resource shipped with VidXP."""

    return (
        files("vidxp")
        .joinpath("assets", "mcp_app", "index.html")
        .read_text(encoding="utf-8")
    )
