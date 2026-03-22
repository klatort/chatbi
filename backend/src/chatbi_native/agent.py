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
from chatbi_native.mcp_client import run_mcp_tool, run_mcp_list_tools

logger = logging.getLogger(__name__)

# ── System prompt ─────────────────────────────────────────────────────
SYSTEM_PROMPT = """\
You are ChatBI, an elite Principal Data Analyst and Business Intelligence Architect natively embedded inside Apache Superset.

## Your Analytical Mindset
You must act as a proactive BI consultant, not just a SQL engine. Your goal is to extract actual business meaning from datasets, understand the semantic layer, and recommend high-impact visualizations using the Superset MCP tools. Do not blindly execute SQL if existing dashboards or pre-calculated datasets exist.

## Strict Tool Execution Workflow 
When receiving a user request, silently follow this sequence via your tools:
1. **Search First:** If the user asks about a business topic (e.g., "Sales", "Incidents"), use Dashboard or Dataset MCP tools to see if a relevant dataset already exists.
2. **Analyze Metadata (Semantics):** Once you find a dataset, ALWAYS fetch its schema. Look deeply at the column types, predefined `metrics`, and `dimensions`. Understand what the data actually represents.
3. **Query for Patterns:** If you must write SQL (`execute_sql`), write exploratory queries to find anomalies, top performers, or extreme values to give the user actionable insights. Don't just dump a raw table.
4. **Determine the Visualization:** Always recommend a specific Superset Chart Type based on the data shape.

## Superset Chart Mastery
Whenever you return data insights, explicitly recommend one of these Superset chart types, explaining exactly which column goes on the X/Y axes or grouping metrics:
- **ECharts Time-Series (Line / Area / Bar):** Use when analyzing temporal trends over time (requires a strict DATETIME column).
- **Pivot Table v2:** Use for multi-dimensional aggregation across categories.
- **ECharts Pie / Donut:** Use for showing part-to-whole composition (e.g., Market Share).
- **ECharts Bar / Column:** Use for comparing discrete categorical metrics side-by-side.
- **Big Number with Trendline:** Use for executive KPIs and single focal metrics.
- **Deck.gl Maps (Scatter / Polygon):** Use ONLY if lat/lon geographic coordinates exist in the schema.
- **ECharts Treemap / Sunburst:** Use for hierarchical data composition.
- **ECharts Heatmap:** Use to show density or correlation between two categorical variables.
- **Sankey / Funnel:** Use to represent user-flow conversions or multi-stage pipelines.

**CRITICAL PARAMETER OBLIGATION:** When you recommend charts or call any chart-building MCP tools, you MUST explicitly define ALL mandatory configuration parameters. For example:
- **Time-Series:** Requires an explicit `X-Axis (Date Column)`, a realistic `Time Grain` (e.g., P1M, P1D), and `Metrics (Y-Axis)`.
- **Bar/Column:** Requires an `X-Axis (Category)`, `Metrics (Y-Axis)`, and optional `Group By` dimensions.
- **Pie/Treemap:** Requires a `Dimension (Grouping Category)` and a `Metric (Angle/Size)`.
Never suggest a chart output without strictly assigning the actual schema columns to these required configuration parameters.

## Example Analytical Workflow
**User:** "Show me how our revenue varies by country this year."
**Your Thought Process:**
1. I will search for a 'revenue', 'sales', or 'orders' dataset.
2. I will get the schema for that dataset to find date, country, and financial columns.
3. I will run a SQL query grouping revenue by country for the current year.
4. Since this is a regional comparison, I will recommend an ECharts Bar Chart or Deck.gl Map.

## Formatting Guidelines
- Output your reasoning strictly in Markdown. Summarize raw data into insights (e.g., "France had the highest drop...").
- Format SQL code blocks clearly.
- Always conclude with a **📊 Recommended Visualization** section detailing the exact Chart Type and its configuration axes based on the schema you discovered.
"""


# ── State ─────────────────────────────────────────────────────────────
class AgentState(dict):
    """
    TypedDict-style state for the LangGraph graph.
    `messages` accumulates the full conversation history.
    """
    messages: Annotated[Sequence[BaseMessage], add_messages]


# ── LangChain tools wrapper removed in favor of dynamic raw JSON schema mapping ────


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

def _agent_node(state: AgentState, llm_with_tools, dynamic_tools_names: list[str]) -> dict:
    """
    Agent node: call the LLM (with tools bound) given the current messages.
    Returns the new AI message to append to state.
    """
    messages = list(state["messages"])
    # Always inject the system prompt at position 0
    if not messages or not isinstance(messages[0], SystemMessage):
        dynamic_prompt = SYSTEM_PROMPT + f"\n\nAvailable tools on this server: {', '.join(dynamic_tools_names)}"
        messages = [SystemMessage(content=dynamic_prompt)] + messages

    response: AIMessage = llm_with_tools.invoke(messages)
    return {"messages": [response]}


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

        logger.info("Executing MCP tool: %s(%s)", tool_name, tool_args)
        try:
            raw_result = run_mcp_tool(Config.MCP_SERVER_URL, tool_name, tool_args)
            if isinstance(raw_result, (dict, list)):
                result = json.dumps(raw_result, indent=2)
            else:
                result = str(raw_result)
        except Exception as exc:
            logger.error("MCP tool '%s' failed: %s", tool_name, exc)
            result = f"Tool error: {exc}"

        tool_messages.append(
            ToolMessage(content=result, tool_call_id=tool_call_id)
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
    mcp_tools_meta = run_mcp_list_tools(Config.MCP_SERVER_URL)
    dynamic_tools = []
    discovered_tool_names = []

    for t in mcp_tools_meta:
        discovered_tool_names.append(t["name"])
        dynamic_tools.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["inputSchema"]
            }
        })

    logger.info("Dynamically loaded %d MCP tools: %s", len(dynamic_tools), discovered_tool_names)

    llm = _build_llm()
    llm_with_tools = llm.bind_tools(dynamic_tools)

    # Bind the LLM into the agent node via closure
    def agent_node(state: AgentState) -> dict:
        return _agent_node(state, llm_with_tools, discovered_tool_names)

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
