"""
Dynamic MCP Tool Discovery
============================
Queries the MCP server at runtime to discover all available tools
(128+ for mcp-superset) and creates LangChain StructuredTool wrappers
for each one.  This replaces static tool definitions with automatic
discovery so the agent can use every tool the server exposes.

Usage::

    from chatbi_native.tools import discover_tools
    tools = discover_tools("http://superset-mcp:5008/sse")
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model

from chatbi_native.mcp_client import run_mcp_tool, run_mcp_list_tools

logger = logging.getLogger(__name__)

# ── Cache ────────────────────────────────────────────────────────────
_tool_cache: dict[str, Any] = {
    "tools": None,
    "url": None,
    "timestamp": 0.0,
}
_CACHE_TTL_SECONDS = 300  # 5 minutes


# ── JSON Schema → Pydantic ──────────────────────────────────────────

# Map JSON Schema types to Python types
_JSON_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
}


def _json_schema_to_pydantic(tool_name: str, schema: dict[str, Any]) -> type[BaseModel]:
    """
    Convert a JSON Schema ``inputSchema`` from the MCP tool listing
    into a dynamically-created Pydantic model that LangChain can use
    as ``args_schema``.
    """
    properties = schema.get("properties", {})
    required_fields = set(schema.get("required", []))

    field_definitions: dict[str, Any] = {}

    for prop_name, prop_schema in properties.items():
        description = prop_schema.get("description", "")
        json_type = prop_schema.get("type", "string")
        default = prop_schema.get("default")
        is_required = prop_name in required_fields

        # Resolve the Python type
        if json_type == "array":
            item_type = prop_schema.get("items", {}).get("type", "string")
            inner = _JSON_TYPE_MAP.get(item_type, str)
            py_type = list[inner]  # type: ignore[valid-type]
        elif json_type == "object":
            # Pass objects as raw dicts — the MCP server will validate
            py_type = dict[str, Any]  # type: ignore[assignment]
        else:
            py_type = _JSON_TYPE_MAP.get(json_type, str)

        # Handle nullable types (anyOf with null)
        any_of = prop_schema.get("anyOf")
        if any_of:
            non_null = [t for t in any_of if t.get("type") != "null"]
            if non_null:
                inner_type = non_null[0].get("type", "string")
                py_type = _JSON_TYPE_MAP.get(inner_type, str)
            py_type = Optional[py_type]  # type: ignore[assignment]

        # Build the Field
        if is_required and default is None:
            field_definitions[prop_name] = (py_type, Field(..., description=description))
        else:
            field_definitions[prop_name] = (py_type, Field(default=default, description=description))

    # Create a unique model name from the tool name
    model_name = "".join(word.capitalize() for word in tool_name.split("_")) + "Schema"
    return create_model(model_name, **field_definitions)  # type: ignore[call-overload]


# ── Tool factory ────────────────────────────────────────────────────

def _make_tool_func(mcp_url: str, tool_name: str):
    """
    Return a closure that calls the named MCP tool via ``run_mcp_tool``.
    """
    def _invoke(**kwargs: Any) -> str:
        try:
            result = run_mcp_tool(mcp_url, tool_name, kwargs)
            if isinstance(result, (dict, list)):
                return json.dumps(result, indent=2, ensure_ascii=False)
            return str(result)
        except Exception as e:
            return f"Superset API Error ({tool_name}): {e}"

    _invoke.__name__ = tool_name
    _invoke.__qualname__ = tool_name
    return _invoke


# ── Public API ──────────────────────────────────────────────────────

def discover_tools(mcp_url: str) -> list[StructuredTool]:
    """
    Query the MCP server for all available tools and return a list of
    LangChain ``StructuredTool`` objects ready to bind to an LLM.

    Results are cached for ``_CACHE_TTL_SECONDS`` to avoid re-querying
    the server on every request.
    """
    now = time.time()

    # Return cached tools if still fresh and URL hasn't changed
    if (
        _tool_cache["tools"] is not None
        and _tool_cache["url"] == mcp_url
        and (now - _tool_cache["timestamp"]) < _CACHE_TTL_SECONDS
    ):
        logger.debug("Using cached MCP tools (%d tools)", len(_tool_cache["tools"]))
        return _tool_cache["tools"]

    logger.info("Discovering tools from MCP server at %s …", mcp_url)

    try:
        raw_tools = run_mcp_list_tools(mcp_url)
    except Exception as e:
        logger.error("Failed to discover MCP tools: %s", e)
        if _tool_cache["tools"] is not None:
            logger.warning("Falling back to stale cached tools")
            return _tool_cache["tools"]
        raise RuntimeError(
            f"Cannot discover MCP tools from {mcp_url}: {e}. "
            "Is the MCP server running?"
        ) from e

    tools: list[StructuredTool] = []

    for raw in raw_tools:
        name: str = raw["name"]
        description: str = raw.get("description", "") or f"MCP tool: {name}"
        input_schema: dict = raw.get("inputSchema", {})

        try:
            args_model = _json_schema_to_pydantic(name, input_schema)
        except Exception as e:
            logger.warning("Skipping tool %s — schema conversion failed: %s", name, e)
            continue

        tool_func = _make_tool_func(mcp_url, name)

        tool = StructuredTool.from_function(
            func=tool_func,
            name=name,
            description=description,
            args_schema=args_model,
        )
        tools.append(tool)

    logger.info("Discovered %d tools from MCP server", len(tools))

    # Update cache
    _tool_cache["tools"] = tools
    _tool_cache["url"] = mcp_url
    _tool_cache["timestamp"] = now

    return tools


def invalidate_cache() -> None:
    """Force re-discovery on the next ``discover_tools`` call."""
    _tool_cache["tools"] = None
    _tool_cache["timestamp"] = 0.0
