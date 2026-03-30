"""
Tool Discovery and Dynamic Tool Creation
=========================================
Dynamically discovers MCP tools and creates LangChain tool wrappers for them.
"""

import json
import logging
import inspect
from typing import Any, Dict, List, Optional, Callable
from functools import wraps

from langchain_core.tools import tool
from pydantic import BaseModel, Field, create_model

from chatbi_native.config import Config
from chatbi_native.mcp_client import run_mcp_list_tools, run_mcp_tool
from chatbi_native.cached_mcp_client import run_mcp_list_tools_cached, run_mcp_tool_cached
from chatbi_native.cache_manager import CacheType

logger = logging.getLogger(__name__)

# Tool categories for organization
TOOL_CATEGORIES = {
    "dataset": ["list_datasets", "get_dataset_schema", "create_dataset", "update_dataset", "delete_dataset"],
    "chart": ["create_superset_chart", "get_chart", "update_chart", "delete_chart", "get_chart_data"],
    "dashboard": ["list_dashboards", "create_dashboard", "update_dashboard", "delete_dashboard", "add_chart_to_dashboard"],
    "sql": ["execute_sql", "get_query_results", "validate_sql_query"],
    "user": ["list_users", "get_user_permissions", "create_user", "update_user_permissions"],
    "cache": ["list_datasets_cached", "get_dataset_schema_cached"],
    "validation": ["validate_chart_parameters", "validate_dataset_access"]
}

def create_dynamic_tool_wrapper(tool_name: str, tool_schema: Dict[str, Any], 
                                use_cache: bool = False, cache_type: Optional[CacheType] = None,
                                ttl_seconds: int = 300) -> Callable:
    """
    Create a dynamic LangChain tool wrapper for an MCP tool.
    
    Args:
        tool_name: Name of the MCP tool
        tool_schema: JSON schema for the tool parameters
        use_cache: Whether to use caching for this tool
        cache_type: Cache type to use if caching is enabled
        ttl_seconds: Time to live for cache entries
    
    Returns:
        LangChain tool function
    """
    
    # Create Pydantic model for the tool arguments
    fields = {}
    required_fields = []
    
    if "properties" in tool_schema:
        for param_name, param_schema in tool_schema["properties"].items():
            field_type = str  # Default to string
            description = param_schema.get("description", "")
            
            # Map JSON schema types to Python types
            param_type = param_schema.get("type", "string")
            if param_type == "integer":
                field_type = int
            elif param_type == "number":
                field_type = float
            elif param_type == "boolean":
                field_type = bool
            elif param_type == "array":
                field_type = List[str]  # Default to list of strings
            
            # Check if parameter is required
            if "required" in tool_schema and param_name in tool_schema["required"]:
                fields[param_name] = (field_type, Field(..., description=description))
                required_fields.append(param_name)
            else:
                # Optional parameter
                fields[param_name] = (Optional[field_type], Field(None, description=description))
    
    # Add user_id parameter for cached tools
    if use_cache:
        fields["user_id"] = (str, Field("anonymous", description="User ID for cache isolation"))
    
    # Create the Pydantic model
    ModelClass = create_model(f"{tool_name.capitalize()}Schema", **fields)
    
    @tool(tool_name, args_schema=ModelClass)
    def dynamic_tool(**kwargs):
        """Dynamically generated tool wrapper for MCP tool: {tool_name}"""
        
        # Extract user_id for caching
        user_id = kwargs.pop("user_id", "anonymous") if use_cache else "anonymous"
        
        # Prepare arguments for MCP call
        mcp_args = {}
        for key, value in kwargs.items():
            if value is not None:  # Skip None values
                mcp_args[key] = value
        
        try:
            if use_cache and cache_type:
                # Use cached version
                logger.info(f"Calling cached MCP tool: {tool_name} (user: {user_id})")
                return run_mcp_tool_cached(
                    Config.MCP_SERVER_URL, 
                    tool_name, 
                    mcp_args, 
                    user_id=user_id,
                    cache_type=cache_type,
                    ttl_seconds=ttl_seconds
                )
            else:
                # Use direct MCP call
                logger.info(f"Calling MCP tool: {tool_name}")
                return run_mcp_tool(Config.MCP_SERVER_URL, tool_name, mcp_args)
                
        except Exception as e:
            logger.error(f"Error calling MCP tool {tool_name}: {e}")
            return f"MCP API Error: {str(e)}"
    
    # Update docstring with actual tool name
    dynamic_tool.__doc__ = f"Dynamically generated tool wrapper for MCP tool: {tool_name}"
    
    return dynamic_tool

def discover_and_create_tools(mcp_url: str = None, use_cache: bool = True) -> List[Any]:
    """
    Discover all available MCP tools and create LangChain tool wrappers.
    
    Args:
        mcp_url: MCP server URL (defaults to Config.MCP_SERVER_URL)
        use_cache: Whether to enable caching for appropriate tools
    
    Returns:
        List of LangChain tool objects
    """
    tools = []
    
    try:
        mcp_url = mcp_url or Config.MCP_SERVER_URL
        
        # Get list of tools from MCP server
        logger.info(f"Discovering MCP tools from {mcp_url}")
        mcp_tools = run_mcp_list_tools_cached(mcp_url, user_id="system", ttl_seconds=3600)
        
        # Map tool names to cache types
        cache_type_map = {
            "list_datasets": CacheType.DATASET,
            "get_dataset_schema": CacheType.DATASET_SCHEMA,
            "list_dashboards": CacheType.DASHBOARD,
            "list_databases": CacheType.DATABASE,
            "get_chart": CacheType.CHART,
            "list_users": CacheType.USER_PERMISSIONS,
        }
        
        for tool_info in mcp_tools:
            tool_name = tool_info.get("name")
            if not tool_name:
                continue
                
            # Skip tools we already have custom implementations for
            if tool_name in ["list_datasets", "get_dataset_schema", "execute_sql", 
                           "create_superset_chart", "add_chart_to_dashboard"]:
                logger.debug(f"Skipping tool with custom implementation: {tool_name}")
                continue
            
            # Determine if this tool should be cached
            cache_type = cache_type_map.get(tool_name)
            should_cache = use_cache and cache_type is not None
            
            try:
                # Create dynamic tool wrapper
                dynamic_tool = create_dynamic_tool_wrapper(
                    tool_name=tool_name,
                    tool_schema=tool_info.get("inputSchema", {}),
                    use_cache=should_cache,
                    cache_type=cache_type,
                    ttl_seconds=300 if should_cache else 0
                )
                
                tools.append(dynamic_tool)
                logger.info(f"Created dynamic tool wrapper for: {tool_name}" + 
                          (" (cached)" if should_cache else ""))
                
            except Exception as e:
                logger.error(f"Failed to create tool wrapper for {tool_name}: {e}")
                continue
        
        logger.info(f"Created {len(tools)} dynamic tool wrappers")
        
    except Exception as e:
        logger.error(f"Failed to discover MCP tools: {e}")
        # Return empty list if discovery fails
        return []
    
    return tools

def get_all_tools(mcp_url: str = None, include_builtin: bool = True, use_cache: bool = True) -> List[Any]:
    """
    Get all available tools (built-in + dynamic).
    
    Args:
        mcp_url: MCP server URL
        include_builtin: Whether to include built-in tools
        use_cache: Whether to enable caching for dynamic tools
    
    Returns:
        List of all LangChain tool objects
    """
    all_tools = []
    
    # Add built-in tools
    if include_builtin:
        from chatbi_native.agent import (
            list_datasets, get_dataset_schema, execute_sql,
            create_superset_chart, add_chart_to_dashboard,
            list_datasets_cached, get_dataset_schema_cached
        )
        
        builtin_tools = [
            list_datasets, get_dataset_schema, execute_sql,
            create_superset_chart, add_chart_to_dashboard,
            list_datasets_cached, get_dataset_schema_cached
        ]
        
        all_tools.extend(builtin_tools)
    
    # Add validation tools
    try:
        from chatbi_native.validation_tools import get_validation_tools
        validation_tools = get_validation_tools()
        all_tools.extend(validation_tools)
        logger.info(f"Added {len(validation_tools)} validation tools")
    except ImportError as e:
        logger.warning(f"Validation tools not available: {e}")
    
    # Add dynamically discovered tools
    dynamic_tools = discover_and_create_tools(mcp_url, use_cache)
    all_tools.extend(dynamic_tools)
    
    logger.info(f"Total tools available: {len(all_tools)}")
    return all_tools

def get_tool_categories() -> Dict[str, List[str]]:
    """
    Get tools organized by category.
    
    Returns:
        Dictionary mapping category names to lists of tool names
    """
    all_tools = get_all_tools(include_builtin=True, use_cache=True)
    
    categorized = {category: [] for category in TOOL_CATEGORIES.keys()}
    categorized["other"] = []
    
    for tool_obj in all_tools:
        tool_name = tool_obj.name
        found = False
        
        for category, tool_names in TOOL_CATEGORIES.items():
            if tool_name in tool_names:
                categorized[category].append(tool_name)
                found = True
                break
        
        if not found:
            categorized["other"].append(tool_name)
    
    return categorized

def get_tool_descriptions() -> List[Dict[str, Any]]:
    """
    Get descriptions of all available tools.
    
    Returns:
        List of dictionaries with tool metadata
    """
    all_tools = get_all_tools(include_builtin=True, use_cache=True)
    
    descriptions = []
    for tool_obj in all_tools:
        try:
            descriptions.append({
                "name": tool_obj.name,
                "description": tool_obj.description or "No description available",
                "args_schema": str(tool_obj.args_schema) if hasattr(tool_obj, 'args_schema') else "No schema",
                "is_cached": "_cached" in tool_obj.name or "cached" in tool_obj.name.lower()
            })
        except Exception as e:
            logger.error(f"Failed to get description for tool {tool_obj.name}: {e}")
    
    return descriptions