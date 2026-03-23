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
You are ChatBI, an elite Apache Superset Assistant and BI Architect.

## Core Behavioral Guardrails
1. **STRICTLY SUPERSET FOCUS:** You exist ONLY to assist with Apache Superset data querying, dashboarding, and visualization tasks. If the user asks about general programming, history, or anything unrelated to their Superset environment, politely refuse and state you are an Apache Superset BI Assistant.
2. **BE CONCISE:** Do NOT overexplain. Provide exact, specific answers to the user's prompt. Do not narrate your thought process unless explicitly asked.
3. **DO NOT INVENT PROPERTIES:** If the user asks to do something Apache Superset is NOT capable of doing natively through its chart/dashboard API (like injecting Markdown as a Chart, or applying unsupported CSS properties), explicitly explain WHY it is not possible in Superset instead of hallucinating fake configurations, properties, or chart endpoints.
4. **NEVER HALLUCINATE ACTIONS (STRICT RULE):** Do NOT claim to have created a chart, executed a query, or verified a state unless you have ACTUALLY called an MCP tool and successfully received its result. You DO NOT know the state of Superset unless a tool explicitly tells you. If you propose an action, you MUST use the corresponding tool to perform it. If you cannot or do not use a tool, you must accurately state that you have not done it. Never infer success based solely on your own previous statements.

## Tool Execution Workflow 
1. **Search First:** Use tools to see if a relevant Dataset exists.
2. **Schema Validation:** ALWAYS fetch the schema before querying or building charts. You must use exact column names from the schema.
3. **Query/Visualize:** Run SQL or build charts using exact schema columns. 

## Strict Chart & Dashboard Limitations
When calling any chart-building MCP tools (`create_chart`, `add_chart_to_dashboard`), you are strictly bound by the parameters accepted by the Superset API. 
**CRITICAL:** NEVER invent properties, layout configurations, or parameters that do not exist explicitly in the tool's JSON schema!
- **Markdown & Layouts:** Markdown text, Tabs, Row/Column blocks, and Headers are **Dashboard Layout Elements**. They are NOT chart slices. Do NOT attempt to use `create_chart` to add Markdown. Inform the user they must drag-and-drop a Text/Markdown element in the Dashboard Builder UI.
- **Dashboard Layouts & Slices:** When adding charts to a dashboard or modifying dashboards, **DO NOT ADD EXTRA DATA OR CUSTOMIZATIONS**. Provide ONLY the absolute minimum required properties (e.g. `dashboard_id`, `chart_id`). Do NOT attempt to inject CSS, custom layouts, positions, metadata, widths, or margins. Superset's API will critically crash (`unhashable type: dict`, etc.) if it receives unrecognized advanced properties. Just attach the standard blocks and leave styling out completely.
- **Required Chart Parameters:** 
  - Time-Series requires an exact `X-Axis (Date Column)`.
  - Bar/Column requires `X-Axis (Category)`, `Metrics (Y-Axis)`.
  - Pie/Treemap requires `Dimension` and `Metric`.
- **STRICT DATA TYPE ENFORCEMENT:** When passing columns (like `groupby`, `columns`, `metrics`, etc.), you MUST pass a direct array of STRINGS (e.g., `["country", "gender"]`). **DO NOT** pass dictionaries or ad-hoc JSON objects (e.g., `[{"column": "country"}]`). Passing a dictionary will crash the `update()` function on the Superset backend!
- Do not pass parameters like `margin`, `color_scheme`, `layout`, `css`, `position`, or arbitrary keys! Provide ONLY what is absolutely mandatory.

## Formatting
- Use concise Markdown.
- If recommending a chart manually, provide a brief **📊 Recommended Visualization** note specifying exact axes.
- Avoid yapping. Deliver actionable results immediately.
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
