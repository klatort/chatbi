"""
LangGraph ReAct Agent — Superset BI Assistant
===============================================
StateGraph implementing a ReAct loop that:
  1. Receives a user message.
  2. Dynamically discovers all 128+ Superset MCP tools at startup.
  3. Calls tools (datasets, charts, dashboards, SQL Lab, security, …)
     to understand the data before generating an answer.
  4. Streams token-by-token responses back to the Flask endpoint.

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
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from chatbi_native.config import Config
from chatbi_native.tools import discover_tools

logger = logging.getLogger(__name__)

# ── System prompt ─────────────────────────────────────────────────────
SYSTEM_PROMPT = """\
You are ChatBI, an elite Apache Superset Data Architect & Administrator.
Your core behavior: Data-Driven, Factual, and Direct.

You have access to the FULL Apache Superset API via 128+ MCP tools covering:
- Datasets: list, get details, create, update, delete, refresh schema, export/import
- Charts: list, get, create, update, delete, copy, get data, export/import
- Dashboards: list, get, create, update, delete, copy, publish/unpublish, export/import
- Dashboard Filters: list, add, update, delete, reset
- SQL Lab: execute queries, format SQL, get results, estimate cost
- Databases: list, get, create, test connections, list schemas/tables/catalogs
- Security: users, roles, permissions, row-level security
- Tags, Groups, System, Reports, Audit tools

WORKFLOW (MANDATORY SEQUENCE FOR DATA QUERIES):
1. DISCOVER: Call `superset_dataset_list` to find the relevant dataset ID.
2. SCHEMA: Call `superset_dataset_get` to fetch exact column names. NEVER guess columns.
3. ACT: Use the appropriate tool (chart, dashboard, SQL, etc.).
4. RESPOND: Only report success after the tool returns a confirmation.

STRICT RULES:
- If you are unsure which tool to use, look at the available tool names and descriptions.
- Never pass UI, CSS, layout, margin, or color properties to chart APIs.
- If a tool returns an error, read the error, correct your arguments, and retry.
- For chart creation use viz_types: echarts_timeseries_bar, echarts_timeseries_line,
  pie, big_number_total, table, etc. Do NOT use deprecated types (bar, line, area).
- Use D3 strftime date formats ("%Y-%m-%d"), NEVER moment.js ("YYYY-MM-DD").
"""


# ── State ─────────────────────────────────────────────────────────────
class AgentState(dict):
    """
    TypedDict-style state for the LangGraph graph.
    `messages` accumulates the full conversation history.
    """
    messages: Annotated[Sequence[BaseMessage], add_messages]



# ── LLM factory ──────────────────────────────────────────────────────

def _build_llm():
    """Instantiate the LLM based on config. Supports OpenAI today."""
    if Config.LLM_PROVIDER == "openai":
        # Force re-read of env in case Flask's debug auto-reloader kept stale os.environ
        from dotenv import load_dotenv, find_dotenv
        import os
        load_dotenv(find_dotenv(usecwd=True), override=True)
        
        actual_base = os.environ.get("OPENAI_API_BASE", Config.OPENAI_API_BASE)
        actual_key = os.environ.get("OPENAI_API_KEY", Config.OPENAI_API_KEY)
        actual_model = os.environ.get("CHATBI_OPENAI_MODEL", Config.OPENAI_MODEL)
        logger.info("Initializing ChatOpenAI with base_url=%s, model=%s", actual_base, actual_model)
        
        kwargs = {
            "model": actual_model,
            "api_key": actual_key,
            "temperature": 0,
            "streaming": True,
        }
        if actual_base:
            kwargs["base_url"] = actual_base
            kwargs["openai_api_base"] = actual_base
            
        return ChatOpenAI(**kwargs)
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


def _make_tools_node(all_tools):
    """
    Create a tools node closure that knows about the discovered tools.
    """
    tools_by_name = {t.name: t for t in all_tools}

    def _tools_node(state: AgentState) -> dict:
        """
        Tools node: execute all pending tool_calls from the latest AI message.
        Returns ToolMessage results to append to state.
        """
        last_message: AIMessage = state["messages"][-1]  # type: ignore
        tool_messages: list[ToolMessage] = []

        for tc in last_message.tool_calls:
            tool_name = tc["name"]
            tool_args = tc["args"]
            tool_call_id = tc["id"]

            logger.info("Executing MCP tool: %s(%s)", tool_name, str(tool_args)[:200])
            if tool_name in tools_by_name:
                tool_obj = tools_by_name[tool_name]
                result = tool_obj.invoke(tool_args)
                if isinstance(result, (dict, list)):
                    content = json.dumps(result, indent=2)
                else:
                    content = str(result)
            else:
                content = f"Tool error: Tool '{tool_name}' not found. Use one of the available tools."

            tool_messages.append(
                ToolMessage(content=content, tool_call_id=tool_call_id, name=tool_name)
            )

        return {"messages": tool_messages}

    return _tools_node


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
    Discovers all available tools from the MCP server dynamically.
    Returns a compiled graph ready for `.stream()` calls.
    """
    # Discover all 128+ tools from the MCP server
    all_tools = discover_tools(Config.MCP_SERVER_URL)
    logger.info("Building graph with %d MCP tools", len(all_tools))

    llm = _build_llm()
    llm_with_tools = llm.bind_tools(all_tools)

    # Bind the LLM into the agent node via closure
    def agent_node(state: AgentState) -> dict:
        return _agent_node(state, llm_with_tools)

    tools_node = _make_tools_node(all_tools)

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)

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
                    yield {
                        "type": "tool_call",
                        "id": tc["id"],
                        "name": tc["name"],
                        "args": tc["args"]
                    }

            elif isinstance(chunk, ToolMessage):
                yield {
                    "type": "tool_result",
                    "id": chunk.tool_call_id,
                    "name": chunk.name,
                    "content": chunk.content[:2000],  # truncate big payloads
                }

        yield {"type": "done"}

    except Exception as exc:
        logger.exception("Agent stream failed")
        yield {"type": "error", "content": str(exc)}
        yield {"type": "done"}
