"""
User Context Management
=======================
Manages user-specific context including permissions, cached metadata, and session state.
"""

import logging
from typing import Dict, Any, Optional
from dataclasses import dataclass, field

from chatbi_native.cache_manager import CacheManager, CacheType, get_cache_manager

logger = logging.getLogger(__name__)

@dataclass
class UserContext:
    """User context with permissions and cached metadata."""
    user_id: str
    cache: CacheManager = field(default_factory=get_cache_manager)
    accessible_datasets: Optional[list] = None
    accessible_dashboards: Optional[list] = None
    accessible_databases: Optional[list] = None
    chart_types: list = field(default_factory=lambda: ["echarts_timeseries", "pie", "big_number_total", "table", "bar"])
    valid_configs: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """Initialize user context by loading cached data."""
        self.load_cached_metadata()
    
    def load_cached_metadata(self):
        """Load cached metadata for the user."""
        # Load datasets
        self.accessible_datasets = self.cache.get(
            CacheType.DATASET, self.user_id, query=""
        )
        
        # Load dashboards
        self.accessible_dashboards = self.cache.get(
            CacheType.DASHBOARD, self.user_id
        )
        
        # Load databases
        self.accessible_databases = self.cache.get(
            CacheType.DATABASE, self.user_id
        )
        
        # Load chart types
        cached_chart_types = self.cache.get(
            CacheType.CHART_TYPES, self.user_id
        )
        if cached_chart_types:
            self.chart_types = cached_chart_types
        
        # Load valid configs
        cached_configs = self.cache.get(
            CacheType.VALID_CONFIGS, self.user_id
        )
        if cached_configs:
            self.valid_configs = cached_configs
        
        logger.debug(f"Loaded cached metadata for user {self.user_id}")
    
    def update_dataset_cache(self, datasets: list, query: str = ""):
        """Update cached datasets for the user."""
        self.accessible_datasets = datasets
        self.cache.set(
            CacheType.DATASET, self.user_id, datasets, 
            ttl_seconds=300, query=query
        )
    
    def update_dashboard_cache(self, dashboards: list):
        """Update cached dashboards for the user."""
        self.accessible_dashboards = dashboards
        self.cache.set(
            CacheType.DASHBOARD, self.user_id, dashboards,
            ttl_seconds=300
        )
    
    def update_database_cache(self, databases: list):
        """Update cached databases for the user."""
        self.accessible_databases = databases
        self.cache.set(
            CacheType.DATABASE, self.user_id, databases,
            ttl_seconds=600
        )
    
    def update_chart_types_cache(self, chart_types: list):
        """Update cached chart types for the user."""
        self.chart_types = chart_types
        self.cache.set(
            CacheType.CHART_TYPES, self.user_id, chart_types,
            ttl_seconds=3600
        )
    
    def update_valid_configs_cache(self, config_type: str, config: Dict[str, Any]):
        """Update cached valid configs for the user."""
        if config_type not in self.valid_configs:
            self.valid_configs[config_type] = []
        
        # Add config if not already present
        if config not in self.valid_configs[config_type]:
            self.valid_configs[config_type].append(config)
            self.cache.set(
                CacheType.VALID_CONFIGS, self.user_id, self.valid_configs,
                ttl_seconds=3600
            )
    
    def get_dataset_by_id(self, datasource_id: int) -> Optional[Dict[str, Any]]:
        """Get dataset by ID from cached datasets."""
        if not self.accessible_datasets:
            return None
        
        for dataset in self.accessible_datasets:
            if isinstance(dataset, dict) and dataset.get('id') == datasource_id:
                return dataset
            elif hasattr(dataset, 'id') and dataset.id == datasource_id:
                return dataset.__dict__ if hasattr(dataset, '__dict__') else dataset
        
        return None
    
    def get_dataset_by_name(self, dataset_name: str) -> Optional[Dict[str, Any]]:
        """Get dataset by name from cached datasets."""
        if not self.accessible_datasets:
            return None
        
        for dataset in self.accessible_datasets:
            if isinstance(dataset, dict):
                name = dataset.get('table_name') or dataset.get('name') or dataset.get('dataset_name')
            else:
                name = getattr(dataset, 'table_name', None) or getattr(dataset, 'name', None) or getattr(dataset, 'dataset_name', None)
            
            if name and dataset_name.lower() in name.lower():
                return dataset if isinstance(dataset, dict) else dataset.__dict__
        
        return None
    
    def invalidate_cache(self, cache_type: Optional[CacheType] = None):
        """Invalidate cache for this user."""
        self.cache.invalidate_user(self.user_id, cache_type)
        
        # Reset in-memory state
        if cache_type == CacheType.DATASET or cache_type is None:
            self.accessible_datasets = None
        if cache_type == CacheType.DASHBOARD or cache_type is None:
            self.accessible_dashboards = None
        if cache_type == CacheType.DATABASE or cache_type is None:
            self.accessible_databases = None
        if cache_type == CacheType.CHART_TYPES or cache_type is None:
            self.chart_types = ["echarts_timeseries", "pie", "big_number_total", "table", "bar"]
        if cache_type == CacheType.VALID_CONFIGS or cache_type is None:
            self.valid_configs = {}
        
        logger.info(f"Invalidated cache for user {self.user_id}" + 
                   (f" (type: {cache_type.value})" if cache_type else ""))
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert user context to dictionary for serialization."""
        return {
            'user_id': self.user_id,
            'accessible_datasets_count': len(self.accessible_datasets) if self.accessible_datasets else 0,
            'accessible_dashboards_count': len(self.accessible_dashboards) if self.accessible_dashboards else 0,
            'accessible_databases_count': len(self.accessible_databases) if self.accessible_databases else 0,
            'chart_types': self.chart_types,
            'valid_configs_count': len(self.valid_configs)
        }


# Global user context registry
_user_contexts: Dict[str, UserContext] = {}

def get_user_context(user_id: str) -> UserContext:
    """
    Get or create user context for a user.
    
    Args:
        user_id: User identifier
    
    Returns:
        UserContext instance
    """
    if user_id not in _user_contexts:
        _user_contexts[user_id] = UserContext(user_id=user_id)
        logger.debug(f"Created new user context for {user_id}")
    
    return _user_contexts[user_id]

def invalidate_user_context(user_id: str, cache_type: Optional[CacheType] = None):
    """
    Invalidate user context cache.
    
    Args:
        user_id: User identifier
        cache_type: Optional cache type to invalidate
    """
    if user_id in _user_contexts:
        _user_contexts[user_id].invalidate_cache(cache_type)
    else:
        # Still invalidate cache even if no context object exists
        cache = get_cache_manager()
        cache.invalidate_user(user_id, cache_type)