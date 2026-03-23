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
from pydantic import BaseModel, Field
from typing import List, Literal, Optional

from chatbi_native.config import Config
from chatbi_native.mcp_client import run_mcp_tool, run_mcp_list_tools

logger = logging.getLogger(__name__)

# ── System prompt ─────────────────────────────────────────────────────
SYSTEM_PROMPT = """\
You are ChatBI, an elite Apache Superset Data Architect.
Your core behavior: Data-Driven, Factual, and Direct.

WORKFLOW (MANDATORY SEQUENCE):
1. SEARCH: Find the relevant dataset ID.
2. SCHEMA VALIDATION: You MUST call `get_dataset_schema` to fetch exact column names. Do not guess.
3. EXECUTE: Call `create_superset_chart` or `add_chart_to_dashboard`.
4. RESPOND: Only report success after the tool returns a confirmation.

STRICT API RULES:
- Never pass UI, CSS, layout, margin, or color properties to the API. 
- Arrays (like metrics or groupby) must be flat arrays of strings: ["col1", "col2"]. Never pass dictionaries.
- If a tool returns an error, read the error message, correct your JSON payload, and call the tool again.
"""


# ── State ─────────────────────────────────────────────────────────────
class AgentState(dict):
    """
    TypedDict-style state for the LangGraph graph.
    `messages` accumulates the full conversation history.
    """
    messages: Annotated[Sequence[BaseMessage], add_messages]


# ── Tools ─────────────────────────────────────────────────────────────

class ListDatasetsSchema(BaseModel):
    query: str = Field(
        default="",
        description="Optional search query to filter datasets by name."
    )

class GetDatasetSchemaSchema(BaseModel):
    datasource_id: int = Field(
        ...,
        description="The exact integer ID of the dataset to get the schema for."
    )

class ExecuteSqlSchema(BaseModel):
    query: str = Field(
        ...,
        description="The SQL query to execute. Must use valid SQL syntax."
    )
    database_id: int = Field(
        ...,
        description="The ID of the database to execute the query against."
    )

class CreateSupersetChartSchema(BaseModel):
    datasource_id: int = Field(
        ..., 
        description="The exact integer ID of the dataset. Must be verified via get_dataset_schema first."
    )
    viz_type: Literal["echarts_timeseries", "pie", "big_number_total", "table", "bar"] = Field(
        ..., 
        description="The chart type. You are RESTRICTED to these exact string values. Do not invent others."
    )
    metrics: List[str] = Field(
        ..., 
        description="A flat array of strings representing the metrics. Example: ['count', 'sum__amount']. FORBIDDEN: Do not pass lists of dictionaries."
    )
    groupby: List[str] = Field(
        default=[], 
        description="A flat array of strings representing columns to group by. Example: ['country_name']. FORBIDDEN: Do not pass dictionaries."
    )

class AddChartToDashboardSchema(BaseModel):
    dashboard_id: int = Field(
        ...,
        description="The integer ID of the dashboard."
    )
    chart_id: int = Field(
        ...,
        description="The integer ID of the chart to add."
    )

@tool("list_datasets", args_schema=ListDatasetsSchema)
def list_datasets(query: str = ""):
    """Lists available datasets matching an optional query."""
    try:
        return run_mcp_tool(Config.MCP_SERVER_URL, "list_datasets", {"query": query})
    except Exception as e:
        return f"Superset API Error: {str(e)}"

@tool("get_dataset_schema", args_schema=GetDatasetSchemaSchema)
def get_dataset_schema(datasource_id: int):
    """Gets the schema (columns, types, etc.) for a specific dataset ID."""
    try:
        return run_mcp_tool(Config.MCP_SERVER_URL, "get_dataset_schema", {"datasource_id": datasource_id})
    except Exception as e:
        return f"Superset API Error: {str(e)}"

@tool("execute_sql", args_schema=ExecuteSqlSchema)
def execute_sql(query: str, database_id: int):
    """Executes a SQL query on a given database ID."""
    try:
        return run_mcp_tool(Config.MCP_SERVER_URL, "execute_sql", {"query": query, "database_id": database_id})
    except Exception as e:
        return f"Superset API Error: {str(e)}"

@tool("create_superset_chart", args_schema=CreateSupersetChartSchema)
def create_superset_chart(datasource_id: int, viz_type: str, metrics: List[str], groupby: Optional[List[str]] = None):
    """
    Creates a new chart in Apache Superset.
    WARNING: Only pass the absolute minimum required properties. Do NOT invent layout, css, or color properties.
    """
    try:
        args = {
            "datasource_id": datasource_id,
            "viz_type": viz_type,
            "metrics": metrics,
            "groupby": groupby or []
        }
        return run_mcp_tool(Config.MCP_SERVER_URL, "create_superset_chart", args)
    except Exception as e:
        return (
            f"Superset API Error: {str(e)}. "
            "Your payload was rejected. Review the JSON schema, remove any forbidden/invented properties (like UI/layout keys), "
            "ensure arrays contain only strings, and try again."
        )

@tool("add_chart_to_dashboard", args_schema=AddChartToDashboardSchema)
def add_chart_to_dashboard(dashboard_id: int, chart_id: int):
    """Adds a newly created chart to an existing dashboard."""
    try:
        return run_mcp_tool(Config.MCP_SERVER_URL, "add_chart_to_dashboard", {"dashboard_id": dashboard_id, "chart_id": chart_id})
    except Exception as e:
        return f"Superset API Error: {str(e)}"

ALL_TOOLS = [list_datasets, get_dataset_schema, execute_sql, create_superset_chart, add_chart_to_dashboard]



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


def _tools_node(state: AgentState) -> dict:
    """
    Tools node: execute all pending tool_calls from the latest AI message.
    Returns ToolMessage results to append to state.
    """
    last_message: AIMessage = state["messages"][-1]  # type: ignore
    tool_messages: list[ToolMessage] = []

    tools_by_name = {t.name: t for t in ALL_TOOLS}

    for tc in last_message.tool_calls:
        tool_name = tc["name"]
        tool_args = tc["args"]
        tool_call_id = tc["id"]

        logger.info("Executing static LangChain tool: %s(%s)", tool_name, tool_args)
        if tool_name in tools_by_name:
            tool_obj = tools_by_name[tool_name]
            result = tool_obj.invoke(tool_args)
            if isinstance(result, (dict, list)):
                content = json.dumps(result, indent=2)
            else:
                content = str(result)
        else:
            content = f"Tool error: Tool {tool_name} not found locally."

        tool_messages.append(
            ToolMessage(content=content, tool_call_id=tool_call_id, name=tool_name)
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
    logger.info("Loading static LangChain tools directly.")

    llm = _build_llm()
    llm_with_tools = llm.bind_tools(ALL_TOOLS)

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
