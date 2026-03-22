"""
MCP Client for Apache Superset FastMCP Server
===============================================
Connects to the Superset MCP server (default: http://localhost:5008/mcp)
and exposes each MCP tool as a plain Python async callable that LangGraph
tools can wrap.

The client uses HTTP+SSE (the standard MCP transport) via the mcp SDK.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from mcp import ClientSession
from mcp.client.sse import sse_client

logger = logging.getLogger(__name__)

# ── Default MCP server URL ────────────────────────────────────────────
_DEFAULT_MCP_URL = "http://superset-mcp:5008/sse"


class SupersetMCPClient:
    """
    Thin async wrapper around the mcp ClientSession.

    Usage::

        async with SupersetMCPClient() as client:
            tools = await client.list_tools()
            result = await client.call_tool("list_datasets", {})
    """

    def __init__(self, url: str = _DEFAULT_MCP_URL) -> None:
        self.url = url
        self._session: ClientSession | None = None
        self._cm = None  # context manager stack

    async def __aenter__(self) -> "SupersetMCPClient":
        self._cm = sse_client(self.url)
        (read, write) = await self._cm.__aenter__()
        self._session = ClientSession(read, write)
        await self._session.__aenter__()
        await self._session.initialize()
        logger.info("Connected to Superset MCP at %s", self.url)
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._session:
            await self._session.__aexit__(*args)
        if self._cm:
            await self._cm.__aexit__(*args)

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return metadata for all tools exposed by the MCP server."""
        assert self._session, "Client not connected"
        result = await self._session.list_tools()
        return [
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.inputSchema,
            }
            for t in result.tools
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Invoke a named MCP tool and return the first text content."""
        assert self._session, "Client not connected"
        result = await self._session.call_tool(name, arguments)
        if result.isError:
            raise RuntimeError(f"MCP tool '{name}' returned error: {result.content}")
        # Return first text block, or all blocks if multiple
        texts = [c.text for c in result.content if hasattr(c, "text")]
        return texts[0] if len(texts) == 1 else texts


def run_mcp_tool(url: str, name: str, arguments: dict[str, Any]) -> Any:
    """
    Synchronous convenience wrapper for calling an MCP tool from
    non-async code (e.g. LangChain tool callbacks run in a thread).
    """

    async def _run() -> Any:
        async with SupersetMCPClient(url=url) as client:
            return await client.call_tool(name, arguments)

    return asyncio.run(_run())
