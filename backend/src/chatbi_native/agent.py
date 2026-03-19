"""
LangGraph ReAct Agent — Superset BI Assistant
===============================================
StateGraph implementing a ReAct loop that:
  1. Receives a user message.
  2. Calls Superset MCP tools (list_datasets, get_schema, execute_sql, …)
     to understand the data before generating an answer.
  3. Streams token-by-token responses back to the Flask endpoint.

Architecture
------------
States:  AgentState (TypedDict)
Nodes:   agent  → llm decides whether to call a tool or finish
         tools  → executes tool calls using the Superset MCP
Edges:   agent ──(tool_calls)──▶ tools ──▶ agent
         agent ──(no tool_calls)──▶ END
"""

from __future__ import annotations

import json
import logging
import os
from typing import Annotated, Any, Generator, Sequence

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from chatbi_native.config import Config
from chatbi_native.mcp_client import run_mcp_tool

logger = logging.getLogger(__name__)

# ── System prompt ─────────────────────────────────────────────────────
SYSTEM_PROMPT = """\
You are ChatBI, an expert data analyst assistant embedded inside Apache Superset.

## Your Capabilities
You have access to the following Superset MCP tools:
- **list_datasets** — discover all available datasets in Superset
- **get_schema** — inspect the columns, types, and sample values for a dataset
- **execute_sql** — run a SQL query against a Superset database and return results

## Rules You MUST Follow
1. **Always inspect before answering.** When a user asks about data, ALWAYS call
   `list_datasets` first (unless they named a specific dataset), then `get_schema`
   on the relevant dataset(s) before writing any SQL.
2. **Show your reasoning.** Before executing SQL, explain the query plan briefly.
3. **Be precise with SQL.** Use the exact table/column names from the schema.
4. **Summarise results clearly.** After executing SQL, present results in a
   human-readable format — tables, bullet points, or charts descriptions.
5. **Ask for clarification** if the question is ambiguous rather than guessing.

## Response Style
- Be concise and data-focused.
- When you don't know something, say so and explain how you would find out.
- Never hallucinate column or table names — always verify via get_schema first.
"""


# ── State ─────────────────────────────────────────────────────────────
class AgentState(dict):
    """
    TypedDict-style state for the LangGraph graph.
    `messages` accumulates the full conversation history.
    """
    messages: Annotated[Sequence[BaseMessage], add_messages]


# ── LangChain tools wrapping MCP calls ───────────────────────────────

def _make_mcp_tool(tool_name: str, description: str, schema: dict[str, Any]):
    """
    Dynamically creates a LangChain @tool that proxies to the Superset MCP.
    Using a closure so each tool carries its own name and schema.
    """
    @tool(tool_name, description=description, args_schema=None, return_direct=False)
    def _tool(**kwargs: Any) -> str:
        try:
            result = run_mcp_tool(Config.MCP_SERVER_URL, tool_name, kwargs)
            if isinstance(result, (dict, list)):
                return json.dumps(result, indent=2)
            return str(result)
        except Exception as exc:
            logger.error("MCP tool '%s' failed: %s", tool_name, exc)
            return f"Error calling {tool_name}: {exc}"

    return _tool


# Pre-built MCP tools — matching Superset FastMCP toolset
list_datasets = _make_mcp_tool(
    "list_datasets",
    "List all datasets (tables/views) available in Apache Superset. "
    "Returns dataset id, name, schema, and database. "
    "Always call this first when the user asks about their data.",
    {},
)

get_schema = _make_mcp_tool(
    "get_schema",
    "Get the column schema for a specific Superset dataset. "
    "Returns column names, types, nullable flags, and sample values. "
    "Pass dataset_id (from list_datasets) or dataset_name.",
    {"dataset_id": {"type": "integer"}, "dataset_name": {"type": "string"}},
)

execute_sql = _make_mcp_tool(
    "execute_sql",
    "Execute a SQL query via Superset's SQL Lab. "
    "Returns up to 1000 rows as a list of dicts. "
    "Always inspect the schema first so column names are correct.",
    {
        "sql": {"type": "string", "description": "The SQL query to execute"},
        "database_id": {"type": "integer", "description": "Superset database id"},
    },
)

get_chart_config = _make_mcp_tool(
    "get_chart_config",
    "Fetch the configuration of an existing Superset chart by its id.",
    {"chart_id": {"type": "integer"}},
)

# Ordered list registered with the LLM
SUPERSET_TOOLS = [list_datasets, get_schema, execute_sql, get_chart_config]


# ── LLM factory ──────────────────────────────────────────────────────

def _build_llm():
    """Instantiate the LLM based on config. Supports OpenAI today."""
    if Config.LLM_PROVIDER == "openai":
        return ChatOpenAI(
            model=Config.OPENAI_MODEL,
            api_key=Config.OPENAI_API_KEY,
            temperature=0,
            streaming=True,
        )
    raise ValueError(f"Unsupported LLM provider: {Config.LLM_PROVIDER}")


# ── Graph nodes ───────────────────────────────────────────────────────

def _agent_node(state: AgentState, llm_with_tools) -> dict:
    """
    Agent node: call the LLM (with tools bound) given the current messages.
    Returns the new AI message to append to state.
    """
    messages = list(state["messages"])
    # Always inject the system prompt at position 0
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=SYSTEM_PROMPT)] + messages

    response: AIMessage = llm_with_tools.invoke(messages)
    return {"messages": [response]}


def _tools_node(state: AgentState) -> dict:
    """
    Tools node: execute all pending tool_calls from the latest AI message.
    Returns ToolMessage results to append to state.
    """
    tool_map = {t.name: t for t in SUPERSET_TOOLS}
    last_message: AIMessage = state["messages"][-1]  # type: ignore
    tool_messages: list[ToolMessage] = []

    for tc in last_message.tool_calls:
        tool_name = tc["name"]
        tool_args = tc["args"]
        tool_call_id = tc["id"]

        logger.info("Executing MCP tool: %s(%s)", tool_name, tool_args)
        if tool_name in tool_map:
            try:
                result = tool_map[tool_name].invoke(tool_args)
            except Exception as exc:
                result = f"Tool error: {exc}"
        else:
            result = f"Unknown tool: {tool_name}"

        tool_messages.append(
            ToolMessage(content=str(result), tool_call_id=tool_call_id)
        )

    return {"messages": tool_messages}


def _should_continue(state: AgentState) -> str:
    """Conditional edge: continue to tools or finish."""
    last: AIMessage = state["messages"][-1]  # type: ignore
    if getattr(last, "tool_calls", None):
        return "tools"
    return END


# ── Graph builder ─────────────────────────────────────────────────────

def build_graph():
    """
    Build and compile the LangGraph StateGraph.
    Returns a compiled graph ready for `.stream()` calls.
    """
    llm = _build_llm()
    llm_with_tools = llm.bind_tools(SUPERSET_TOOLS)

    # Bind the LLM into the agent node via closure
    def agent_node(state: AgentState) -> dict:
        return _agent_node(state, llm_with_tools)

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", _tools_node)

    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", _should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")

    return graph.compile()


# ── Streaming helper ──────────────────────────────────────────────────

def stream_agent(
    user_message: str,
    history: list[dict] | None = None,
    mcp_url: str | None = None,
) -> Generator[dict, None, None]:
    """
    Stream the agent's response for a given user message.

    Yields dicts matching the SSE payload schema:
        {"type": "token",     "content": "..."}
        {"type": "tool_call", "name": "...", "args": {...}}
        {"type": "tool_result", "name": "...", "content": "..."}
        {"type": "done"}
        {"type": "error",     "content": "..."}
    """
    if mcp_url:
        Config.MCP_SERVER_URL = mcp_url

    # Build conversation history
    messages: list[BaseMessage] = []
    for msg in (history or []):
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))

    messages.append(HumanMessage(content=user_message))

    compiled_graph = build_graph()
    initial_state: AgentState = {"messages": messages}  # type: ignore

    try:
        for event in compiled_graph.stream(initial_state, stream_mode="messages"):
            # event is a tuple (message_chunk, metadata) when stream_mode="messages"
            if isinstance(event, tuple):
                chunk, _meta = event
            else:
                chunk = event

            if isinstance(chunk, AIMessage):
                # Stream text tokens
                if chunk.content:
                    yield {"type": "token", "content": chunk.content}
                # Stream tool-call start events
                for tc in getattr(chunk, "tool_calls", []):
                    yield {"type": "tool_call", "name": tc["name"], "args": tc["args"]}

            elif isinstance(chunk, ToolMessage):
                yield {
                    "type": "tool_result",
                    "name": "mcp_tool",
                    "content": chunk.content[:2000],  # truncate big payloads
                }

        yield {"type": "done"}

    except Exception as exc:
        logger.exception("Agent stream failed")
        yield {"type": "error", "content": str(exc)}
        yield {"type": "done"}
