"""
Performance optimization: caching and metrics.

Implements:
- Translation pair caching (Redis-backed)
- Performance metrics tracking
"""

import hashlib
import time
from typing import Optional
import redis.asyncio as redis

from app.redis_backend import REDIS_URL

# Redis connection for cache (separate from main Redis)
_cache_redis: Optional[redis.Redis] = None

# Cache TTL (24 hours)
CACHE_TTL = 86400

# Cache key prefix
CACHE_PREFIX = "dub:cache:translate:"


def _get_cache_redis() -> redis.Redis:
    """Get or create cache Redis connection."""
    global _cache_redis
    if _cache_redis is None:
        _cache_redis = redis.from_url(REDIS_URL, decode_responses=True)
    return _cache_redis


def _cache_key(src_text: str, src_lang: str, tgt_lang: str) -> str:
    """Generate cache key for translation pair."""
    key_data = f"{src_lang}:{tgt_lang}:{src_text}"
    key_hash = hashlib.sha256(key_data.encode("utf-8")).hexdigest()
    return f"{CACHE_PREFIX}{key_hash}"


async def get_cached_translation(src_text: str, src_lang: str, tgt_lang: str) -> Optional[str]:
    """Get cached translation if available."""
    if not src_text:
        return None

    try:
        r = _get_cache_redis()
        key = _cache_key(src_text, src_lang, tgt_lang)
        cached = await r.get(key)
        if cached:
            return cached
    except Exception:
        # Cache miss or error - continue without cache
        pass
    return None


async def set_cached_translation(
    src_text: str, src_lang: str, tgt_lang: str, translated: str
) -> None:
    """Cache a translation."""
    if not src_text or not translated:
        return

    try:
        r = _get_cache_redis()
        key = _cache_key(src_text, src_lang, tgt_lang)
        await r.setex(key, CACHE_TTL, translated)
    except Exception:
        # Cache write failure - continue without caching
        pass
