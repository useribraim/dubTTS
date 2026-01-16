"""
Unit tests for performance optimization (caching).
"""

import pytest
from app.performance import (
    get_cached_translation,
    set_cached_translation,
    _cache_key,
    CACHE_PREFIX,
)


@pytest.mark.asyncio
async def test_cache_key_generation():
    """Test that cache keys are generated correctly."""
    key1 = _cache_key("Hello", "en", "es")
    key2 = _cache_key("Hello", "en", "es")
    key3 = _cache_key("Hello", "en", "ru")
    
    # Same input should generate same key
    assert key1 == key2
    
    # Different target language should generate different key
    assert key1 != key3
    
    # Key should have correct prefix
    assert key1.startswith(CACHE_PREFIX)


@pytest.mark.asyncio
async def test_translation_caching(redis_client):
    """Test translation caching functionality."""
    # Set cache Redis connection
    import app.performance as perf_module
    perf_module._cache_redis = redis_client
    
    src_text = "Hello, world!"
    src_lang = "en"
    tgt_lang = "es"
    translated = "¡Hola, mundo!"
    
    # Cache miss initially
    cached = await get_cached_translation(src_text, src_lang, tgt_lang)
    assert cached is None
    
    # Set cache
    await set_cached_translation(src_text, src_lang, tgt_lang, translated)
    
    # Cache hit
    cached = await get_cached_translation(src_text, src_lang, tgt_lang)
    assert cached == translated
    
    # Different text should be cache miss
    cached = await get_cached_translation("Different text", src_lang, tgt_lang)
    assert cached is None


@pytest.mark.asyncio
async def test_cache_empty_text(redis_client):
    """Test that empty text doesn't cache."""
    import app.performance as perf_module
    perf_module._cache_redis = redis_client
    
    # Empty source text
    cached = await get_cached_translation("", "en", "es")
    assert cached is None
    
    # Setting empty text should not cache
    await set_cached_translation("", "en", "es", "translated")
    cached = await get_cached_translation("", "en", "es")
    assert cached is None


@pytest.mark.asyncio
async def test_cache_different_languages(redis_client):
    """Test that different language pairs are cached separately."""
    import app.performance as perf_module
    perf_module._cache_redis = redis_client
    
    text = "Hello"
    
    # Cache en->es
    await set_cached_translation(text, "en", "es", "Hola")
    
    # Cache en->ru
    await set_cached_translation(text, "en", "ru", "Привет")
    
    # Both should be retrievable
    es_translation = await get_cached_translation(text, "en", "es")
    ru_translation = await get_cached_translation(text, "en", "ru")
    
    assert es_translation == "Hola"
    assert ru_translation == "Привет"
