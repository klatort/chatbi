"""
Cached MCP Client for Apache Superset FastMCP Server
=====================================================
Extends the base MCP client with user-aware caching for Superset metadata.

Features:
- Cache Superset datasets, schemas, charts, and dashboards per user
- TTL-based cache invalidation
- Automatic cache population on first access
- Permission-aware caching (respects user permissions)
"""

import asyncio
import logging
from typing import Any, Optional, Callable
from functools import wraps

from chatbi_native.cache_manager import CacheManager, CacheType, get_cache_manager
from chatbi_native.mcp_client import SupersetMCPClient, run_mcp_tool as base_run_mcp_tool

logger = logging.getLogger(__name__)

class CachedSupersetMCPClient(SupersetMCPClient):
    """
    MCP client with user-aware caching.
    
    Extends the base SupersetMCPClient to add caching for frequently accessed
    Superset metadata like datasets, schemas, charts, and dashboards.
    """
    
    def __init__(self, url: str = "http://superset-mcp:5008/sse", user_id: str = "anonymous"):
        super().__init__(url)
        self.user_id = user_id
        self.cache = get_cache_manager()
    
    async def list_datasets_cached(self, query: str = "") -> Any:
        """Get datasets with caching."""
        cache_key = f"query={query}"
        cached = self.cache.get(CacheType.DATASET, self.user_id, query=query)
        if cached is not None:
            logger.debug(f"Cache hit for datasets (user: {self.user_id}, query: {query})")
            return cached
        
        logger.debug(f"Cache miss for datasets (user: {self.user_id}, query: {query})")
        result = await self.call_tool("list_datasets", {"query": query})
        self.cache.set(CacheType.DATASET, self.user_id, result, ttl_seconds=300, query=query)
        return result
    
    async def get_dataset_schema_cached(self, datasource_id: int) -> Any:
        """Get dataset schema with caching."""
        cached = self.cache.get(CacheType.DATASET_SCHEMA, self.user_id, datasource_id=datasource_id)
        if cached is not None:
            logger.debug(f"Cache hit for dataset schema (user: {self.user_id}, datasource_id: {datasource_id})")
            return cached
        
        logger.debug(f"Cache miss for dataset schema (user: {self.user_id}, datasource_id: {datasource_id})")
        result = await self.call_tool("get_dataset_schema", {"datasource_id": datasource_id})
        self.cache.set(CacheType.DATASET_SCHEMA, self.user_id, result, ttl_seconds=600, datasource_id=datasource_id)
        return result
    
    async def list_dashboards_cached(self) -> Any:
        """Get dashboards with caching."""
        cached = self.cache.get(CacheType.DASHBOARD, self.user_id)
        if cached is not None:
            logger.debug(f"Cache hit for dashboards (user: {self.user_id})")
            return cached
        
        logger.debug(f"Cache miss for dashboards (user: {self.user_id})")
        result = await self.call_tool("list_dashboards", {})
        self.cache.set(CacheType.DASHBOARD, self.user_id, result, ttl_seconds=300)
        return result
    
    async def list_databases_cached(self) -> Any:
        """Get databases with caching."""
        cached = self.cache.get(CacheType.DATABASE, self.user_id)
        if cached is not None:
            logger.debug(f"Cache hit for databases (user: {self.user_id})")
            return cached
        
        logger.debug(f"Cache miss for databases (user: {self.user_id})")
        result = await self.call_tool("list_databases", {})
        self.cache.set(CacheType.DATABASE, self.user_id, result, ttl_seconds=600)
        return result
    
    async def get_chart_cached(self, chart_id: int) -> Any:
        """Get chart details with caching."""
        cached = self.cache.get(CacheType.CHART, self.user_id, chart_id=chart_id)
        if cached is not None:
            logger.debug(f"Cache hit for chart (user: {self.user_id}, chart_id: {chart_id})")
            return cached
        
        logger.debug(f"Cache miss for chart (user: {self.user_id}, chart_id: {chart_id})")
        result = await self.call_tool("get_chart", {"chart_id": chart_id})
        self.cache.set(CacheType.CHART, self.user_id, result, ttl_seconds=300, chart_id=chart_id)
        return result
    
    async def prefetch_user_metadata(self) -> dict:
        """Pre-fetch and cache critical metadata for the current user."""
        try:
            # Fetch datasets
            datasets = await self.list_datasets_cached("")
            
            # Fetch dashboards if available
            dashboards = []
            try:
                dashboards = await self.list_dashboards_cached()
            except Exception as e:
                logger.warning(f"Could not fetch dashboards: {e}")
            
            # Fetch databases if available
            databases = []
            try:
                databases = await self.list_databases_cached()
            except Exception as e:
                logger.warning(f"Could not fetch databases: {e}")
            
            # Cache chart types (hardcoded for now, could be fetched from MCP if available)
            chart_types = ["echarts_timeseries", "pie", "big_number_total", "table", "bar"]
            self.cache.set(CacheType.CHART_TYPES, self.user_id, chart_types, ttl_seconds=3600)
            
            # Cache empty valid configs placeholder
            self.cache.set(CacheType.VALID_CONFIGS, self.user_id, {}, ttl_seconds=3600)
            
            return {
                "datasets_count": len(datasets) if isinstance(datasets, list) else 0,
                "dashboards_count": len(dashboards) if isinstance(dashboards, list) else 0,
                "databases_count": len(databases) if isinstance(databases, list) else 0,
                "chart_types": chart_types
            }
            
        except Exception as e:
            logger.error(f"Failed to prefetch metadata for user {self.user_id}: {e}")
            return {"error": str(e)}
    
    def invalidate_cache(self, cache_type: Optional[CacheType] = None):
        """Invalidate cache for the current user."""
        self.cache.invalidate_user(self.user_id, cache_type)
        logger.info(f"Invalidated cache for user {self.user_id}" + 
                   (f" (type: {cache_type.value})" if cache_type else ""))


def cached_mcp_tool(cache_type: CacheType, ttl_seconds: int = 300):
    """
    Decorator to add caching to MCP tool calls.
    
    Usage:
        @cached_mcp_tool(CacheType.DATASET, ttl_seconds=300)
        def list_datasets(user_id: str, query: str = ""):
            return run_mcp_tool(Config.MCP_SERVER_URL, "list_datasets", {"query": query})
    """
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(user_id: str, *args, **kwargs):
            cache = get_cache_manager()
            
            # Generate cache key from function arguments (excluding user_id)
            cache_key_args = []
            for arg in args:
                cache_key_args.append(str(arg))
            for key, value in kwargs.items():
                cache_key_args.append(f"{key}={value}")
            
            # Check cache
            cached = cache.get(cache_type, user_id, *cache_key_args)
            if cached is not None:
                logger.debug(f"Cache hit for {func.__name__} (user: {user_id})")
                return cached
            
            # Cache miss, call function
            logger.debug(f"Cache miss for {func.__name__} (user: {user_id})")
            result = func(*args, **kwargs)
            
            # Store in cache
            cache.set(cache_type, user_id, result, ttl_seconds, *cache_key_args)
            return result
        return wrapper
    return decorator


# Cached versions of the synchronous run_mcp_tool functions
def run_mcp_tool_cached(url: str, name: str, arguments: dict[str, Any], 
                       user_id: str = "anonymous", cache_type: Optional[CacheType] = None,
                       ttl_seconds: int = 300) -> Any:
    """
    Synchronous cached wrapper for calling MCP tools.
    
    Args:
        url: MCP server URL
        name: Tool name
        arguments: Tool arguments
        user_id: User identifier for cache isolation
        cache_type: Type of cache entry (if None, no caching)
        ttl_seconds: Time to live for cache entry
    
    Returns:
        Tool result
    """
    if cache_type is None:
        # No caching requested
        return base_run_mcp_tool(url, name, arguments)
    
    cache = get_cache_manager()
    
    # Generate cache key
    cache_key_parts = [name]
    for key, value in sorted(arguments.items()):
        cache_key_parts.append(f"{key}={value}")
    cache_key = ":".join(cache_key_parts)
    
    # Check cache
    cached = cache.get(cache_type, user_id, cache_key=cache_key)
    if cached is not None:
        logger.debug(f"Cache hit for {name} (user: {user_id})")
        return cached
    
    # Cache miss, call tool
    logger.debug(f"Cache miss for {name} (user: {user_id})")
    result = base_run_mcp_tool(url, name, arguments)
    
    # Store in cache
    cache.set(cache_type, user_id, result, ttl_seconds, cache_key=cache_key)
    return result


def run_mcp_list_tools_cached(url: str, user_id: str = "anonymous", 
                             ttl_seconds: int = 3600) -> list[dict[str, Any]]:
    """
    Synchronous cached wrapper for listing MCP tools.
    
    Args:
        url: MCP server URL
        user_id: User identifier for cache isolation
        ttl_seconds: Time to live for cache entry
    
    Returns:
        List of tool metadata
    """
    from chatbi_native.mcp_client import run_mcp_list_tools as base_run_mcp_list_tools
    
    cache = get_cache_manager()
    cache_type = CacheType.USER_PERMISSIONS  # Using USER_PERMISSIONS for tool list
    
    # Check cache
    cached = cache.get(cache_type, user_id, tool="list_tools")
    if cached is not None:
        logger.debug(f"Cache hit for list_tools (user: {user_id})")
        return cached
    
    # Cache miss, call tool
    logger.debug(f"Cache miss for list_tools (user: {user_id})")
    result = base_run_mcp_list_tools(url)
    
    # Store in cache
    cache.set(cache_type, user_id, result, ttl_seconds, tool="list_tools")
    return result