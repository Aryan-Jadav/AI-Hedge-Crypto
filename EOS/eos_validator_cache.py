# Last updated: 2026-02-21
"""
EOS Validator Cache - Caching layer for market data

Reduces API calls by caching frequently accessed data with TTL.
"""

import time
from dataclasses import dataclass, field
from typing import Dict, Any, Optional
from datetime import datetime


@dataclass
class CacheEntry:
    """Single cache entry with TTL."""
    data: Any
    timestamp: float
    ttl: int  # seconds
    
    def is_expired(self) -> bool:
        return time.time() - self.timestamp > self.ttl


class EOSValidatorCache:
    """
    Simple in-memory cache with TTL for market data.
    
    Default TTLs:
    - market_data: 60s (NIFTY, BANKNIFTY levels)
    - vix: 60s
    - sector_perf: 300s (5 min)
    - news: 300s (5 min)
    - stock_context: 120s (2 min)
    """
    
    DEFAULT_TTLS = {
        "market_data": 60,
        "vix": 60,
        "sector_perf": 300,
        "news": 300,
        "stock_context": 120,
        "validation_history": 3600,  # 1 hour
    }
    
    def __init__(self):
        self._cache: Dict[str, CacheEntry] = {}
        self._hits = 0
        self._misses = 0
    
    def get(self, key: str) -> Optional[Any]:
        """Get value from cache if exists and not expired."""
        if key in self._cache:
            entry = self._cache[key]
            if not entry.is_expired():
                self._hits += 1
                return entry.data
            else:
                del self._cache[key]
        self._misses += 1
        return None
    
    def set(self, key: str, data: Any, ttl: int = None, category: str = None):
        """Set value in cache with TTL."""
        if ttl is None:
            ttl = self.DEFAULT_TTLS.get(category, 60)
        
        self._cache[key] = CacheEntry(
            data=data,
            timestamp=time.time(),
            ttl=ttl
        )
    
    def invalidate(self, key: str):
        """Remove specific key from cache."""
        if key in self._cache:
            del self._cache[key]
    
    def invalidate_category(self, prefix: str):
        """Remove all keys starting with prefix."""
        keys_to_remove = [k for k in self._cache if k.startswith(prefix)]
        for key in keys_to_remove:
            del self._cache[key]
    
    def clear(self):
        """Clear entire cache."""
        self._cache.clear()
        self._hits = 0
        self._misses = 0
    
    def get_stats(self) -> Dict:
        """Get cache statistics."""
        total = self._hits + self._misses
        hit_rate = (self._hits / total * 100) if total > 0 else 0
        
        return {
            "entries": len(self._cache),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": f"{hit_rate:.1f}%"
        }
    
    def cleanup_expired(self):
        """Remove all expired entries."""
        expired = [k for k, v in self._cache.items() if v.is_expired()]
        for key in expired:
            del self._cache[key]
        return len(expired)

