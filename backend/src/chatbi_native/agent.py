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
import sys
from typing import Annotated, Any, Generator, Sequence

# Force UTF-8 encoding for stdout/stderr to prevent Docker ASCII locale crashes
if hasattr(sys.stdout, 'reconfigure') and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure') and sys.stderr.encoding.lower() != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

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

You have access to the FULL Apache Superset API via 128+ MCP tools.
CRITICAL: ONLY pass parameters that exist in each tool's schema.
NEVER invent, guess, or add extra parameters. If unsure, check the tool description.

══════════════════════════════════════════════════════
MANDATORY WORKFLOW (follow this EVERY time):
══════════════════════════════════════════════════════
1. CONTEXT:  The user's message may include a hidden `[System Context: ...]` string
             indicating their exact current dashboard or chart. If this context is present,
             assume they are asking about that specific entity and fetch it immediately
             using its ID without doing a discovery search first.
2. DISCOVER: Call `superset_dataset_list` to find dataset IDs ONLY if not in context.
3. SCHEMA:   Call `superset_dataset_get` with `dataset_id` to get exact column names.
             NEVER guess column names — always verify first.
4. ACT:      Use the correct tool with ONLY the parameters it accepts.
5. VERIFY:   Check the response for errors. If error, fix parameters and retry.

══════════════════════════════════════════════════════
CHART CREATION — superset_chart_create
══════════════════════════════════════════════════════
Parameters: slice_name (str, REQUIRED), viz_type (str, REQUIRED),
            datasource_id (int, REQUIRED), datasource_type (str, default "table"),
            params (str|None), query_context (str|None), dashboards (list[int]|None)

▸ `params` must be a JSON STRING, e.g.: '{"metrics": ["count"], "groupby": ["city"]}'
▸ Do NOT pass metrics/groupby as top-level parameters — they go INSIDE params.
▸ Always include `granularity_sqla` in params if the dataset has a time column.

VALID viz_types:
  echarts_timeseries_bar, echarts_timeseries_line, echarts_timeseries_smooth,
  echarts_timeseries_step, echarts_area, pie, big_number_total, big_number,
  table, pivot_table_v2, mixed_timeseries, funnel, gauge_chart, radar,
  word_cloud, box_plot, bubble_v2, waterfall, heatmap_v2, histogram_v2,
  treemap_v2, sunburst_v2, sankey_v2, country_map, world_map

DEPRECATED viz_types (DO NOT USE — causes "not registered" error):
  bar → echarts_timeseries_bar,  line → echarts_timeseries_line,
  area → echarts_area,  dist_bar → echarts_timeseries_bar,
  heatmap → heatmap_v2,  histogram → histogram_v2,  pie (OK, not deprecated)

Example — bar chart:
  superset_chart_create(
    slice_name="Sales by Region",
    viz_type="echarts_timeseries_bar",
    datasource_id=5,
    params='{"metrics": ["sum__revenue"], "groupby": ["region"], "granularity_sqla": "order_date"}'
  )

Example — KPI card:
  superset_chart_create(
    slice_name="Total Revenue",
    viz_type="big_number_total",
    datasource_id=5,
    params='{"metrics": ["sum__revenue"], "y_axis_format": ",d", "header_font_size": 0.27}'
  )

══════════════════════════════════════════════════════
CHART UPDATE — superset_chart_update
══════════════════════════════════════════════════════
Parameters: chart_id (int, REQUIRED), slice_name, viz_type, params,
            query_context, dashboards, confirm_params_replace (bool)

▸ When passing `params`: you MUST set confirm_params_replace=True.
▸ params REPLACES ALL parameters — first call superset_chart_get to read current
  params, modify what you need, then pass the FULL params JSON back.

══════════════════════════════════════════════════════
DASHBOARD CREATION — superset_dashboard_create
══════════════════════════════════════════════════════
Parameters: dashboard_title (str, REQUIRED), slug (str|None),
            published (bool), json_metadata (str|None),
            css (str|None), position_json (str|None), roles (list[int]|None)

▸ Use `dashboard_title`, NOT `title` or `name`.
▸ Do NOT pass `charts`, `chart_ids`, or `widgets` — those don't exist.
▸ To add charts: bind them at chart creation time via the `dashboards` parameter
  in superset_chart_create, or update the dashboard's position_json.

Example — simple dashboard:
  superset_dashboard_create(dashboard_title="Sales Dashboard", published=True)

══════════════════════════════════════════════════════
DASHBOARD UPDATE — superset_dashboard_update
══════════════════════════════════════════════════════
Parameters: dashboard_id (int, REQUIRED), dashboard_title, slug, published,
            json_metadata, css, position_json, roles

▸ Only pass the fields you want to change.

══════════════════════════════════════════════════════
SQL EXECUTION — superset_sqllab_execute
══════════════════════════════════════════════════════
Parameters: database_id (int, REQUIRED), sql (str, REQUIRED),
            schema (str|None), catalog (str|None), tab_name, template_params

▸ Use `database_id`, NOT `datasource_id`.
▸ The `sql` parameter is the query string.
▸ Max 1000 rows returned. DDL/DML (DROP, DELETE, UPDATE, INSERT) is blocked.

══════════════════════════════════════════════════════
DATE FORMATS (CRITICAL)
══════════════════════════════════════════════════════
Superset uses D3 strftime format ONLY. moment.js formats render as literal text!
  CORRECT: "%Y-%m-%d" → 2026-03-05
  WRONG:   "YYYY-MM-DD" → shows literal "YYYY-MM-DD"

══════════════════════════════════════════════════════
ANTI-PATTERNS (NEVER DO THESE)
══════════════════════════════════════════════════════
✗ Do NOT invent parameters not in the tool schema
✗ Do NOT pass metrics/groupby as top-level chart_create args (put them in params JSON)
✗ Do NOT use deprecated viz_types (bar, line, area, dist_bar, etc.)
✗ Do NOT pass UI/CSS/layout/margin/color properties to chart APIs
✗ Do NOT pass `title` to dashboard_create (use `dashboard_title`)
✗ Do NOT pass `query` to sqllab_execute (use `sql`)
✗ Do NOT use moment.js date formats ("YYYY-MM-DD") — use D3 ("%Y-%m-%d")
✗ Do NOT guess column names — always call superset_dataset_get first
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

            # Safely encode tool args for logging (Docker ASCII locale workaround)
            safe_args_log = json.dumps(tool_args, ensure_ascii=True)
            logger.info("Executing MCP tool: %s(%s)", tool_name, safe_args_log[:200])
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
