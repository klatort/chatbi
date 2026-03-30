"""
Cache Manager for Superset Metadata
===================================
Simple in-memory cache for Superset metadata with user isolation.
"""

import time
import logging
from typing import Any, Dict, Optional
from enum import Enum

logger = logging.getLogger(__name__)

class CacheType(Enum):
    DATASET = "dataset"
    DATASET_SCHEMA = "dataset_schema"
    CHART = "chart"
    DASHBOARD = "dashboard"
    USER_PERMISSIONS = "user_permissions"
    DATABASE = "database"
    CHART_TYPES = "chart_types"
    VALID_CONFIGS = "valid_configs"

class CacheManager:
    """Simple in-memory cache manager for Superset metadata."""
    
    def __init__(self):
        self._cache: Dict[str, Dict] = {}
        self.stats = {'hits': 0, 'misses': 0, 'sets': 0}
    
    def _generate_key(self, cache_type: CacheType, user_id: str, *args, **kwargs) -> str:
        """Generate cache key from parameters."""
        key_parts = [cache_type.value, user_id]
        key_parts.extend(str(arg) for arg in args)
        for k, v in sorted(kwargs.items()):
            key_parts.append(f"{k}={v}")
        return ":".join(key_parts)
    
    def get(self, cache_type: CacheType, user_id: str, *args, **kwargs) -> Optional[Any]:
        """Get cached value."""
        key = self._generate_key(cache_type, user_id, *args, **kwargs)
        if key in self._cache:
            entry = self._cache[key]
            if entry['expires_at'] > time.time():
                self.stats['hits'] += 1
                return entry['value']
            else:
                # Expired
                del self._cache[key]
        
        self.stats['misses'] += 1
        return None
    
    def set(self, cache_type: CacheType, user_id: str, value: Any, 
            ttl_seconds: int = 300, *args, **kwargs) -> str:
        """Set cache value."""
        key = self._generate_key(cache_type, user_id, *args, **kwargs)
        self._cache[key] = {
            'value': value,
            'expires_at': time.time() + ttl_seconds,
            'cache_type': cache_type.value,
            'user_id': user_id
        }
        self.stats['sets'] += 1
        logger.debug(f"Cached {cache_type.value} for user {user_id}")
        return key
    
    def delete(self, cache_type: CacheType, user_id: str, *args, **kwargs) -> bool:
        """Delete cache entry."""
        key = self._generate_key(cache_type, user_id, *args, **kwargs)
        if key in self._cache:
            del self._cache[key]
            return True
        return False
    
    def invalidate_user(self, user_id: str, cache_type: Optional[CacheType] = None):
        """Invalidate cache for user."""
        keys_to_delete = []
        for key, entry in self._cache.items():
            if entry['user_id'] == user_id:
                if cache_type is None or entry['cache_type'] == cache_type.value:
                    keys_to_delete.append(key)
        
        for key in keys_to_delete:
            del self._cache[key]
        
        logger.info(f"Invalidated {len(keys_to_delete)} cache entries for user {user_id}")

# Global cache instance
_cache_instance = None

def get_cache_manager() -> CacheManager:
    """Get or create global cache manager instance."""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = CacheManager()
    return _cache_instance
