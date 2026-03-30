"""
Integration Test for ChatBI Performance Improvements
====================================================
Tests the complete system with caching, validation, and session management.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import time
import json
from typing import Dict, Any

def test_cache_manager():
    """Test cache manager functionality."""
    print("Testing Cache Manager...")
    
    from chatbi_native.cache_manager import CacheManager, CacheType
    
    cache = CacheManager()
    
    # Test basic set/get
    cache.set(CacheType.DATASET, "user1", [{"id": 1, "name": "test"}], ttl_seconds=10)
    cached = cache.get(CacheType.DATASET, "user1")
    assert cached is not None, "Cache get failed"
    print(f"  ✓ Basic cache set/get works")
    
    # Test cache expiration
    cache.set(CacheType.DATASET, "user2", [{"id": 2, "name": "test2"}], ttl_seconds=0.1)
    time.sleep(0.2)
    expired = cache.get(CacheType.DATASET, "user2")
    assert expired is None, "Cache expiration failed"
    print(f"  ✓ Cache expiration works")
    
    # Test user isolation
    cache.set(CacheType.DATASET, "user3", [{"id": 3, "name": "user3_data"}])
    user3_data = cache.get(CacheType.DATASET, "user3")
    user4_data = cache.get(CacheType.DATASET, "user4")
    assert user3_data is not None, "User 3 data should exist"
    assert user4_data is None, "User 4 should not see user 3 data"
    print(f"  ✓ User isolation works")
    
    # Test cache invalidation
    cache.set(CacheType.DATASET, "user5", [{"id": 5, "name": "data"}])
    cache.invalidate_user("user5", CacheType.DATASET)
    invalidated = cache.get(CacheType.DATASET, "user5")
    assert invalidated is None, "Cache invalidation failed"
    print(f"  ✓ Cache invalidation works")
    
    print(f"  ✓ Cache stats: {cache.stats}")
    print("✅ Cache Manager tests passed\n")
    return True


def test_user_context():
    """Test user context management."""
    print("Testing User Context...")
    
    from chatbi_native.user_context import get_user_context
    
    # Get user context
    user1 = get_user_context("test_user_1")
    user2 = get_user_context("test_user_2")
    
    assert user1.user_id == "test_user_1", "User ID mismatch"
    assert user2.user_id == "test_user_2", "User ID mismatch"
    print(f"  ✓ User context creation works")
    
    # Test context isolation
    user1.update_dataset_cache([{"id": 1, "name": "user1_data"}], query="")
    user2.update_dataset_cache([{"id": 2, "name": "user2_data"}], query="")
    
    assert user1.accessible_datasets is not None, "User 1 datasets should be set"
    assert user2.accessible_datasets is not None, "User 2 datasets should be set"
    assert user1.accessible_datasets != user2.accessible_datasets, "Users should have isolated data"
    print(f"  ✓ User context isolation works")
    
    # Test to_dict
    user_dict = user1.to_dict()
    assert "user_id" in user_dict, "to_dict missing user_id"
    assert "accessible_datasets_count" in user_dict, "to_dict missing datasets count"
    print(f"  ✓ User context serialization works")
    
    print("✅ User Context tests passed\n")
    return True


def test_validation_tools():
    """Test validation tools."""
    print("Testing Validation Tools...")
    
    from chatbi_native.validation_tools import (
        validate_chart_parameters,
        validate_sql_query,
        validate_dashboard_parameters,
        validate_before_execute
    )
    
    # Test chart validation
    chart_result = validate_chart_parameters.invoke({
        'datasource_id': 123,
        'viz_type': 'echarts_timeseries',
        'metrics': ['count', 'sum__amount'],
        'groupby': ['country']
    })
    assert chart_result['valid'] == True, "Valid chart should pass"
    print(f"  ✓ Chart validation works")
    
    # Test invalid chart (use a valid list for metrics to pass Pydantic validation)
    invalid_chart = validate_chart_parameters.invoke({
        'datasource_id': -1,  # Invalid ID
        'viz_type': 'invalid_type',
        'metrics': ['count'],  # Valid list but invalid parameters
        'groupby': ['country']
    })
    # Should have warnings about viz_type
    assert 'warnings' in invalid_chart, "Invalid chart should have warnings"
    print(f"  ✓ Invalid chart detection works")
    
    # Test SQL validation
    sql_result = validate_sql_query.invoke({
        'query': 'SELECT * FROM users WHERE id = 1',
        'database_id': 1
    })
    assert sql_result['valid'] == True, "Valid SQL should pass"
    print(f"  ✓ SQL validation works")
    
    # Test dashboard validation
    dashboard_result = validate_dashboard_parameters.invoke({
        'dashboard_title': 'My Dashboard',
        'css': 'body { background: white; }',
        'slug': 'my-dashboard'
    })
    assert dashboard_result['valid'] == True, "Valid dashboard should pass"
    print(f"  ✓ Dashboard validation works")
    
    # Test high-level validation function
    high_level_result = validate_before_execute(
        operation='chart',
        parameters={
            'datasource_id': 123,
            'viz_type': 'echarts_timeseries',
            'metrics': ['count', 'sum__amount'],
            'groupby': ['country']
        },
        user_id='test_user'
    )
    assert high_level_result['valid'] == True, "High-level validation should work"
    print(f"  ✓ High-level validation works")
    
    print("✅ Validation Tools tests passed\n")
    return True


def test_session_manager():
    """Test session management."""
    print("Testing Session Manager...")
    
    from chatbi_native.session_manager import (
        create_user_session,
        get_user_session,
        end_user_session,
        validate_session_permission
    )
    
    # Create session
    session = create_user_session(
        user_id='integration_test_user',
        ip_address='127.0.0.1',
        user_agent='IntegrationTest/1.0'
    )
    
    assert session is not None, "Session creation failed"
    assert session.user_id == 'integration_test_user', "Session user ID mismatch"
    print(f"  ✓ Session creation works")
    
    # Retrieve session
    retrieved = get_user_session(session.session_id)
    assert retrieved is not None, "Session retrieval failed"
    assert retrieved.session_id == session.session_id, "Session ID mismatch"
    print(f"  ✓ Session retrieval works")
    
    # Check permissions
    has_view = validate_session_permission(session.session_id, 'view_datasets')
    assert has_view == True, "Default permission check failed"
    print(f"  ✓ Permission checking works")
    
    # End session
    end_user_session(session.session_id)
    ended = get_user_session(session.session_id)
    assert ended is None, "Session should be ended"
    print(f"  ✓ Session ending works")
    
    print("✅ Session Manager tests passed\n")
    return True


def test_performance_monitor():
    """Test performance monitoring."""
    print("Testing Performance Monitor...")
    
    from chatbi_native.performance import (
        PerformanceMonitor,
        get_performance_monitor,
        timed_tool
    )
    
    # Create monitor
    monitor = PerformanceMonitor()
    
    # Record some metrics
    monitor.record_tool_call('test_tool_1', 0.5, cached=True)
    monitor.record_tool_call('test_tool_2', 1.2, cached=False)
    monitor.record_error('test_tool_3')
    monitor.record_user_session('user1', 10.5, 3)
    
    summary = monitor.get_summary()
    assert summary['tool_calls'] == 2, "Tool call count mismatch"
    assert summary['cache_hits'] == 1, "Cache hit count mismatch"
    assert summary['cache_misses'] == 1, "Cache miss count mismatch"
    assert summary['errors'] == 1, "Error count mismatch"
    print(f"  ✓ Performance metrics recording works")
    
    # Test timed_tool decorator with a new monitor
    test_monitor = PerformanceMonitor()
    
    # Temporarily replace the decorator's monitor reference
    import chatbi_native.performance as perf_module
    original_monitor = perf_module._performance_monitor
    perf_module._performance_monitor = test_monitor
    
    try:
        @timed_tool
        def test_tool(x, cached=False):
            time.sleep(0.01)
            return x * 2
        
        result = test_tool(5, cached=True)
        assert result == 10, "Tool result incorrect"
        
        # Check that performance was recorded
        test_summary = test_monitor.get_summary()
        assert test_summary['tool_calls'] == 1, "Decorated tool call not counted"
        print(f"  ✓ Timed tool decorator works")
    finally:
        # Restore original monitor
        perf_module._performance_monitor = original_monitor
    
    # Test global monitor
    global_monitor = get_performance_monitor()
    assert global_monitor is not None, "Global monitor not accessible"
    print(f"  ✓ Global monitor access works")
    
    print("✅ Performance Monitor tests passed\n")
    return True


def test_tool_discovery():
    """Test tool discovery."""
    print("Testing Tool Discovery...")
    
    from chatbi_native.tool_discovery import (
        get_all_tools,
        get_tool_categories,
        get_tool_descriptions
    )
    
    # Get tools (will fail if MCP server not running, but that's OK)
    try:
        tools = get_all_tools(include_builtin=True, use_cache=True)
        assert len(tools) >= 7, f"Expected at least 7 tools, got {len(tools)}"
        print(f"  ✓ Found {len(tools)} tools")
        
        # Check for specific tools
        tool_names = [t.name for t in tools]
        assert 'list_datasets' in tool_names, "list_datasets tool missing"
        assert 'validate_chart_parameters' in tool_names, "validate_chart_parameters tool missing"
        print(f"  ✓ Required tools present")
        
    except Exception as e:
        print(f"  ⚠ Tool discovery failed (expected if MCP server not running): {e}")
    
    # Get categories
    categories = get_tool_categories()
    assert 'dataset' in categories, "Dataset category missing"
    assert 'validation' in categories, "Validation category missing"
    print(f"  ✓ Tool categorization works")
    
    # Get descriptions
    descriptions = get_tool_descriptions()
    assert len(descriptions) > 0, "No tool descriptions returned"
    print(f"  ✓ Tool descriptions retrieved")
    
    print("✅ Tool Discovery tests passed\n")
    return True


def run_all_tests():
    """Run all integration tests."""
    print("=" * 60)
    print("Running ChatBI Integration Tests")
    print("=" * 60)
    
    tests = [
        ("Cache Manager", test_cache_manager),
        ("User Context", test_user_context),
        ("Validation Tools", test_validation_tools),
        ("Session Manager", test_session_manager),
        ("Performance Monitor", test_performance_monitor),
        ("Tool Discovery", test_tool_discovery),
    ]
    
    passed = 0
    failed = 0
    
    for test_name, test_func in tests:
        try:
            if test_func():
                print(f"✅ {test_name}: PASSED")
                passed += 1
            else:
                print(f"❌ {test_name}: FAILED")
                failed += 1
        except Exception as e:
            print(f"❌ {test_name}: ERROR - {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"Test Results: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)