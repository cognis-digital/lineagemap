"""LINEAGEMAP MCP server — exposes scan() as an MCP tool for Cognis.Studio."""
from __future__ import annotations
from lineagemap.core import scan, to_json

def serve() -> int:
    """Start an MCP stdio server. Requires the optional 'mcp' extra:
        pip install "cognis-lineagemap[mcp]"
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception:
        print("Install the MCP extra: pip install 'cognis-lineagemap[mcp]'")
        return 1
    app = FastMCP("lineagemap")

    @app.tool()
    def lineagemap_scan(target: str) -> str:
        """Column-level lineage extracted from SQL and dbt. Returns JSON findings."""
        return to_json(scan(target))

    app.run()
    return 0
