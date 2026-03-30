"""
Performance Optimization for ChatBI Agent
=========================================
Optimizations for tool calls, caching, and response times.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Callable
from functools import wraps
from concurrent.futures import ThreadPoolExecutor, as_completed

from chatbi_native.cache_manager import CacheManager, CacheType, get_cache_manager
from chatbi_native.user_context import get_user_context

logger = logging.getLogger(__name__)

# Global thread pool for parallel operations
_thread_pool = ThreadPoolExecutor(max_workers=10)

class PerformanceMonitor:
    """Monitor and track performance metrics."""
    
    def __init__(self):
        self.metrics = {
            'tool_calls': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'total_response_time': 0.0,
            'tool_response_times': {},
            'errors': 0,
            'user_sessions': {}
        }
    
    def record_tool_call(self, tool_name: str, response_time: float, cached: bool = False):
        """Record a tool call with timing."""
        self.metrics['tool_calls'] += 1
        self.metrics['total_response_time'] += response_time
        
        if cached:
            self.metrics['cache_hits'] += 1
        else:
            self.metrics['cache_misses'] += 1
        
        if tool_name not in self.metrics['tool_response_times']:
            self.metrics['tool_response_times'][tool_name] = {
                'count': 0,
                'total_time': 0.0,
                'avg_time': 0.0,
                'cached_count': 0
            }
        
        tool_metrics = self.metrics['tool_response_times'][tool_name]
        tool_metrics['count'] += 1
        tool_metrics['total_time'] += response_time
        tool_metrics['avg_time'] = tool_metrics['total_time'] / tool_metrics['count']
        if cached:
            tool_metrics['cached_count'] += 1
    
    def record_error(self, tool_name: str):
        """Record an error for a tool."""
        self.metrics['errors'] += 1
    
    def record_user_session(self, user_id: str, duration: float, tool_count: int):
        """Record user session metrics."""
        if user_id not in self.metrics['user_sessions']:
            self.metrics['user_sessions'][user_id] = {
                'session_count': 0,
                'total_duration': 0.0,
                'total_tools': 0
            }
        
        user_metrics = self.metrics['user_sessions'][user_id]
        user_metrics['session_count'] += 1
        user_metrics['total_duration'] += duration
        user_metrics['total_tools'] += tool_count
    
    def get_summary(self) -> Dict[str, Any]:
        """Get performance summary."""
        summary = self.metrics.copy()
        
        # Calculate averages
        if summary['tool_calls'] > 0:
            summary['avg_response_time'] = summary['total_response_time'] / summary['tool_calls']
            summary['cache_hit_rate'] = summary['cache_hits'] / summary['tool_calls']
        else:
            summary['avg_response_time'] = 0.0
            summary['cache_hit_rate'] = 0.0
        
        # Calculate error rate
        total_operations = summary['tool_calls'] + summary['errors']
        if total_operations > 0:
            summary['error_rate'] = summary['errors'] / total_operations
        else:
            summary['error_rate'] = 0.0
        
        return summary
    
    def reset(self):
        """Reset all metrics."""
        self.metrics = {
            'tool_calls': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'total_response_time': 0.0,
            'tool_response_times': {},
            'errors': 0,
            'user_sessions': {}
        }


# Global performance monitor
_performance_monitor = PerformanceMonitor()

def get_performance_monitor() -> PerformanceMonitor:
    """Get the global performance monitor instance."""
    return _performance_monitor


def timed_tool(tool_func: Callable):
    """
    Decorator to time tool execution and record performance metrics.
    
    Args:
        tool_func: The tool function to wrap
    
    Returns:
        Wrapped function with timing
    """
    @wraps(tool_func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        cached = kwargs.get('cached', False) or 'cached' in tool_func.__name__.lower()
        
        try:
            result = tool_func(*args, **kwargs)
            response_time = time.time() - start_time
            
            # Record metrics
            _performance_monitor.record_tool_call(
                tool_name=tool_func.__name__,
                response_time=response_time,
                cached=cached
            )
            
            # Add timing info to result if it's a dict
            if isinstance(result, dict):
                result['_performance'] = {
                    'response_time_ms': round(response_time * 1000, 2),
                    'cached': cached
                }
            
            return result
            
        except Exception as e:
            response_time = time.time() - start_time
            _performance_monitor.record_error(tool_func.__name__)
            logger.error(f"Tool {tool_func.__name__} failed after {response_time:.2f}s: {e}")
            raise
    
    return wrapper


def batch_tool_calls(tool_calls: List[Dict[str, Any]], max_workers: int = 5) -> List[Any]:
    """
    Execute multiple tool calls in parallel using thread pool.
    
    Args:
        tool_calls: List of dicts with 'tool' (callable) and 'args' (dict)
        max_workers: Maximum number of parallel workers
    
    Returns:
        List of results in the same order as tool_calls
    """
    if not tool_calls:
        return []
    
    # Use smaller pool for small batches
    workers = min(max_workers, len(tool_calls))
    
    with ThreadPoolExecutor(max_workers=workers) as executor:
        # Submit all tasks
        future_to_index = {
            executor.submit(tc['tool'], **tc.get('args', {})): i
            for i, tc in enumerate(tool_calls)
        }
        
        # Collect results in order
        results = [None] * len(tool_calls)
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            try:
                results[index] = future.result()
            except Exception as e:
                results[index] = {"error": str(e)}
    
    return results


def predictive_cache_prefetch(user_id: str, tool_patterns: List[str]):
    """
    Pre-fetch data for tools that are likely to be used based on patterns.
    
    Args:
        user_id: User identifier
        tool_patterns: List of tool name patterns to pre-fetch for
    """
    user_context = get_user_context(user_id)
    cache = get_cache_manager()
    
    # Check what's already cached
    cached_datasets = cache.get(CacheType.DATASET, user_id, query="")
    cached_dashboards = cache.get(CacheType.DASHBOARD, user_id)
    
    # Pre-fetch datasets if not cached and likely needed
    if not cached_datasets and any('dataset' in pattern.lower() for pattern in tool_patterns):
        logger.info(f"Predictive prefetch: fetching datasets for user {user_id}")
        # This would actually fetch datasets in a real implementation
        # For now, just log the intent
    
    # Pre-fetch dashboards if not cached and likely needed
    if not cached_dashboards and any('dashboard' in pattern.lower() for pattern in tool_patterns):
        logger.info(f"Predictive prefetch: fetching dashboards for user {user_id}")
        # This would actually fetch dashboards in a real implementation


def optimize_tool_descriptions(tool_descriptions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Optimize tool descriptions for better LLM understanding.
    
    Args:
        tool_descriptions: List of tool metadata dictionaries
    
    Returns:
        Optimized tool descriptions
    """
    optimized = []
    
    for tool in tool_descriptions:
        # Create optimized description
        name = tool.get('name', '')
        description = tool.get('description', '')
        
        # Add caching hint for cached tools
        if '_cached' in name or 'cached' in description.lower():
            description = f"[CACHED] {description} - Uses cache for better performance"
        
        # Add validation hint for validation tools
        if 'validate' in name.lower():
            description = f"[VALIDATION] {description} - Use before executing operations"
        
        # Add performance hints
        if 'list' in name.lower() or 'get' in name.lower():
            description = f"{description} - Consider using cached version if available"
        
        optimized_tool = tool.copy()
        optimized_tool['description'] = description
        
        # Add usage examples for common tools
        if 'dataset' in name.lower():
            if 'list' in name.lower():
                optimized_tool['example'] = "Use to find datasets by name or explore available data"
            elif 'schema' in name.lower():
                optimized_tool['example'] = "Use to get column names and types before creating charts"
        
        optimized.append(optimized_tool)
    
    return optimized


def get_performance_insights() -> Dict[str, Any]:
    """
    Get performance insights and recommendations.
    
    Returns:
        Dictionary with insights and recommendations
    """
    monitor = get_performance_monitor()
    summary = monitor.get_summary()
    
    insights = {
        'summary': summary,
        'recommendations': [],
        'bottlenecks': []
    }
    
    # Analyze tool performance
    slow_tools = []
    for tool_name, metrics in summary.get('tool_response_times', {}).items():
        if metrics['avg_time'] > 1.0:  # Tools taking more than 1 second on average
            slow_tools.append({
                'tool': tool_name,
                'avg_time': metrics['avg_time'],
                'call_count': metrics['count'],
                'cache_hit_rate': metrics.get('cached_count', 0) / metrics['count'] if metrics['count'] > 0 else 0
            })
    
    if slow_tools:
        insights['bottlenecks'] = slow_tools
        insights['recommendations'].append(
            f"Consider adding caching for slow tools: {', '.join([t['tool'] for t in slow_tools])}"
        )
    
    # Analyze cache performance
    cache_hit_rate = summary.get('cache_hit_rate', 0)
    if cache_hit_rate < 0.3:
        insights['recommendations'].append(
            f"Low cache hit rate ({cache_hit_rate:.1%}). Consider increasing cache TTL or pre-fetching data."
        )
    
    # Analyze error rate
    error_rate = summary.get('error_rate', 0)
    if error_rate > 0.1:
        insights['recommendations'].append(
            f"High error rate ({error_rate:.1%}). Consider improving validation or error handling."
        )
    
    return insights


# Performance-optimized versions of common operations
@timed_tool
def batch_get_dataset_schemas(datasource_ids: List[int], user_id: str = "anonymous") -> Dict[int, Any]:
    """
    Get multiple dataset schemas in batch, with caching.
    
    Args:
        datasource_ids: List of dataset IDs
        user_id: User identifier for cache
    
    Returns:
        Dictionary mapping dataset ID to schema
    """
    cache = get_cache_manager()
    results = {}
    uncached_ids = []
    
    # Check cache first
    for datasource_id in datasource_ids:
        cached = cache.get(CacheType.DATASET_SCHEMA, user_id, datasource_id=datasource_id)
        if cached is not None:
            results[datasource_id] = {"cached": True, "schema": cached}
        else:
            uncached_ids.append(datasource_id)
    
    # Fetch uncached schemas in parallel
    if uncached_ids:
        from chatbi_native.mcp_client import run_mcp_tool
        
        tool_calls = []
        for datasource_id in uncached_ids:
            tool_calls.append({
                'tool': run_mcp_tool,
                'args': {
                    'url': 'http://superset-mcp:5008/sse',
                    'name': 'get_dataset_schema',
                    'arguments': {'datasource_id': datasource_id}
                }
            })
        
        # Execute in parallel
        batch_results = batch_tool_calls(tool_calls)
        
        # Process results and cache them
        for datasource_id, result in zip(uncached_ids, batch_results):
            if not isinstance(result, str) or "Error" not in result:
                # Cache successful results
                cache.set(CacheType.DATASET_SCHEMA, user_id, result, ttl_seconds=600, datasource_id=datasource_id)
                results[datasource_id] = {"cached": False, "schema": result}
            else:
                results[datasource_id] = {"error": result}
    
    return results


@timed_tool
def prefetch_user_data(user_id: str) -> Dict[str, Any]:
    """
    Pre-fetch all commonly used data for a user.
    
    Args:
        user_id: User identifier
    
    Returns:
        Dictionary with pre-fetch results
    """
    from chatbi_native.mcp_client import run_mcp_tool
    
    cache = get_cache_manager()
    results = {}
    
    try:
        # Fetch datasets
        datasets = run_mcp_tool('http://superset-mcp:5008/sse', 'list_datasets', {})
        cache.set(CacheType.DATASET, user_id, datasets, ttl_seconds=300)
        results['datasets'] = len(datasets) if isinstance(datasets, list) else 0
        
        # Fetch dashboards
        dashboards = run_mcp_tool('http://superset-mcp:5008/sse', 'list_dashboards', {})
        cache.set(CacheType.DASHBOARD, user_id, dashboards, ttl_seconds=300)
        results['dashboards'] = len(dashboards) if isinstance(dashboards, list) else 0
        
        # Update user context
        user_context = get_user_context(user_id)
        user_context.update_dataset_cache(datasets, query="")
        user_context.update_dashboard_cache(dashboards)
        
        logger.info(f"Pre-fetched data for user {user_id}: {results}")
        
    except Exception as e:
        logger.error(f"Failed to pre-fetch data for user {user_id}: {e}")
        results['error'] = str(e)
    
    return results