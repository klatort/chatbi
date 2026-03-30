"""
Dynamic LangGraph ReAct Agent — Superset BI Assistant with Full MCP Tool Exposure
=================================================================================
Agent that dynamically discovers and exposes all available MCP tools.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any, Generator, Sequence, List

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
from chatbi_native.tool_discovery import get_all_tools, get_tool_categories, get_tool_descriptions
from chatbi_native.user_context import get_user_context

logger = logging.getLogger(__name__)

# ── Enhanced System Prompt with Full Tool Access ─────────────────────────────
DYNAMIC_SYSTEM_PROMPT = """\
You are ChatBI, an elite Apache Superset Data Architect with access to ALL available MCP tools.
Your core behavior: Data-Driven, Factual, and Direct.

CRITICAL RULES (ZERO TOLERANCE):
1. **ALWAYS VALIDATE BEFORE EXECUTION**: Before calling any tool that creates or modifies resources,
   you MUST call the appropriate validation tool first.
2. **NEVER GUESS PARAMETERS**: Always fetch schema and metadata before using datasets.
3. **USE CACHED DATA**: Check user context for cached datasets, schemas, and dashboards before fetching.
4. **RESPECT USER PERMISSIONS**: Only access resources the user has permission to see.
5. **USE THE RIGHT TOOL**: Choose the most specific tool for the task.

WORKFLOW (MANDATORY SEQUENCE):
1. **CONTEXT CHECK**: Check user context for cached datasets/dashboards. If missing, fetch them.
2. **DATASET DISCOVERY**: Use `list_datasets_cached` to find relevant datasets.
3. **SCHEMA VALIDATION**: You MUST call `get_dataset_schema_cached` to fetch exact column names. Do not guess.
4. **PARAMETER VALIDATION**: Before creating charts, call validation tools to check parameters.
5. **EXECUTE**: Call the appropriate tool with validated parameters.
6. **RESPOND**: Only report success after the tool returns a confirmation.

TOOL CATEGORIES:
{tool_categories}

STRICT API RULES:
- Never pass UI, CSS, layout, margin, or color properties to the API.
- Arrays (like metrics or groupby) must be flat arrays of strings: ["col1", "col2"]. Never pass dictionaries.
- If a tool returns an error, read the error message, correct your JSON payload, and call the tool again.
- Always use cached versions of tools when available (tools ending with _cached).

USER CONTEXT:
The user has access to the following resources (cached):
{{user_context}}

Always check the user context first before making API calls. If data is missing from cache, fetch it once and cache it for future use.
"""

# ── State with User Context ────────────────────────────────────────────────
class DynamicAgentState(dict):
    """
    Enhanced state for the LangGraph graph with user context.
    `messages` accumulates the full conversation history.
    `user_id` identifies the user for caching and permissions.
    `user_context` contains cached metadata for the user.
    """
    messages: Annotated[Sequence[BaseMessage], add_messages]
    user_id: str
    user_context: dict


# ── Dynamic Agent Functions ────────────────────────────────────────────────

def _build_dynamic_llm():
    """Build LLM with dynamically discovered tools."""
    # Get all available tools
    all_tools = get_all_tools(mcp_url=Config.MCP_SERVER_URL, include_builtin=True, use_cache=True)
    
    # Build tool categories for system prompt
    categories = get_tool_categories()
    tool_categories_text = ""
    for category, tools in categories.items():
        if tools:
            tool_categories_text += f"\n- **{category.upper()}**: {', '.join(sorted(tools)[:10])}"
            if len(tools) > 10:
                tool_categories_text += f" and {len(tools) - 10} more..."
    
    system_prompt = DYNAMIC_SYSTEM_PROMPT.format(tool_categories=tool_categories_text)
    
    llm = ChatOpenAI(
        model=Config.OPENAI_MODEL,
        temperature=0,
        api_key=Config.OPENAI_API_KEY,
        base_url=Config.OPENAI_BASE_URL,
    )
    
    return llm.bind_tools(all_tools), system_prompt, all_tools


def _dynamic_agent_node(state: DynamicAgentState, llm_with_tools, system_prompt: str) -> dict:
    """
    Agent node: decide whether to call a tool or respond.
    """
    messages = list(state["messages"])
    
    # Always inject the system prompt at position 0
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=system_prompt)] + messages
    
    # Add user context if available
    user_id = state.get("user_id", "anonymous")
    user_context = get_user_context(user_id)
    
    # Add user context to system prompt
    context_message = f"\n\nUSER CONTEXT:\n{json.dumps(user_context.to_dict(), indent=2)}"
    messages[0] = SystemMessage(content=messages[0].content + context_message)
    
    response: AIMessage = llm_with_tools.invoke(messages)
    return {"messages": [response]}


def _dynamic_tools_node(state: DynamicAgentState, all_tools: List) -> dict:
    """
    Tools node: execute all pending tool_calls from the latest AI message.
    Returns ToolMessage results to append to state.
    """
    last_message: AIMessage = state["messages"][-1]  # type: ignore
    tool_messages: list[ToolMessage] = []
    
    # Get user_id from state if available
    user_id = state.get("user_id", "anonymous")
    
    # Create lookup dictionary for tools
    tools_by_name = {t.name: t for t in all_tools}
    
    for tc in last_message.tool_calls:
        tool_name = tc["name"]
        tool_args = tc["args"]
        tool_call_id = tc["id"]
        
        logger.info("Executing tool: %s(%s)", tool_name, tool_args)
        
        # Add user_id to tool args for tools that support it
        if tool_name in ["list_datasets_cached", "get_dataset_schema_cached", "validate_dataset_access"]:
            tool_args["user_id"] = user_id
        
        if tool_name in tools_by_name:
            tool_obj = tools_by_name[tool_name]
            try:
                result = tool_obj.invoke(tool_args)
                if isinstance(result, (dict, list)):
                    content = json.dumps(result, indent=2)
                else:
                    content = str(result)
            except Exception as e:
                content = f"Tool execution error: {str(e)}"
                logger.error(f"Error executing tool {tool_name}: {e}")
        else:
            content = f"Tool error: Tool {tool_name} not found."
            logger.warning(f"Tool {tool_name} not found in available tools")
        
        tool_messages.append(
            ToolMessage(content=content, tool_call_id=tool_call_id, name=tool_name)
        )
    
    return {"messages": tool_messages}


def _should_continue(state: DynamicAgentState) -> str:
    """Conditional edge: continue to tools or finish."""
    last: AIMessage = state["messages"][-1]  # type: ignore
    if getattr(last, "tool_calls", None):
        return "tools"
    return END


# ── Graph builder ─────────────────────────────────────────────────────

def build_dynamic_graph():
    """
    Build and compile the dynamic LangGraph StateGraph with all available tools.
    Returns a compiled graph ready for `.stream()` calls.
    """
    logger.info("Building dynamic agent graph with all available tools")
    
    llm_with_tools, system_prompt, all_tools = _build_dynamic_llm()
    
    # Bind the LLM and tools into the nodes via closure
    def agent_node(state: DynamicAgentState) -> dict:
        return _dynamic_agent_node(state, llm_with_tools, system_prompt)
    
    def tools_node(state: DynamicAgentState) -> dict:
        return _dynamic_tools_node(state, all_tools)
    
    graph = StateGraph(DynamicAgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", _should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    
    return graph.compile()


# ── Streaming helper ──────────────────────────────────────────────────

def stream_dynamic_agent(
    user_message: str,
    history: list[dict] | None = None,
    mcp_url: str | None = None,
    user_id: str = "anonymous",
) -> Generator[dict, None, None]:
    """
    Stream the dynamic agent's response for a given user message.
    
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
    
    compiled_graph = build_dynamic_graph()
    initial_state: DynamicAgentState = {"messages": messages, "user_id": user_id}  # type: ignore
    
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
                        "args": tc["args"],
                    }
            
            elif isinstance(chunk, ToolMessage):
                yield {
                    "type": "tool_result",
                    "name": chunk.name,
                    "content": chunk.content[:500] + "..." if len(chunk.content) > 500 else chunk.content,
                }
        
        yield {"type": "done"}
    
    except Exception as exc:
        logger.exception("Unhandled error in dynamic agent stream")
        yield {"type": "error", "content": str(exc)}
        yield {"type": "done"}


# ── Utility Functions ──────────────────────────────────────────────────

def get_available_tools() -> List[dict]:
    """
    Get list of all available tools with descriptions.
    
    Returns:
        List of tool metadata dictionaries
    """
    return get_tool_descriptions()


def get_tool_categories_info() -> dict:
    """
    Get tools organized by category.
    
    Returns:
        Dictionary mapping category names to lists of tool names
    """
    return get_tool_categories()


def prewarm_user_cache(user_id: str, mcp_url: str = None) -> dict:
    """
    Pre-warm cache for a user by fetching commonly used metadata.
    
    Args:
        user_id: User identifier
        mcp_url: Optional MCP server URL override
    
    Returns:
        Dictionary with pre-warming results
    """
    if mcp_url:
        Config.MCP_SERVER_URL = mcp_url
    
    user_context = get_user_context(user_id)
    
    # Try to fetch datasets if not cached
    if not user_context.accessible_datasets:
        try:
            from chatbi_native.mcp_client import run_mcp_tool
            datasets = run_mcp_tool(Config.MCP_SERVER_URL, "list_datasets", {})
            user_context.update_dataset_cache(datasets, query="")
        except Exception as e:
            logger.warning(f"Failed to pre-warm datasets for user {user_id}: {e}")
    
    # Try to fetch dashboards if not cached
    if not user_context.accessible_dashboards:
        try:
            from chatbi_native.mcp_client import run_mcp_tool
            dashboards = run_mcp_tool(Config.MCP_SERVER_URL, "list_dashboards", {})
            user_context.update_dashboard_cache(dashboards)
        except Exception as e:
            logger.warning(f"Failed to pre-warm dashboards for user {user_id}: {e}")
    
    return {
        "user_id": user_id,
        "datasets_cached": user_context.accessible_datasets is not None,
        "dashboards_cached": user_context.accessible_dashboards is not None,
        "databases_cached": user_context.accessible_databases is not None,
        "chart_types": user_context.chart_types
    }