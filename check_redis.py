#!/usr/bin/env python3
"""
Quick script to check if Redis is running and accessible.
"""

import os
import sys
import asyncio
import redis.asyncio as redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

async def check_redis():
    print(f"Checking Redis connection at: {REDIS_URL}")
    print()
    
    try:
        r = redis.from_url(REDIS_URL, decode_responses=False)
        await r.ping()
        print("[OK] Redis is connected and responding!")
        
        # Test basic operations
        test_key = "test:connection"
        await r.set(test_key, "ok")
        value = await r.get(test_key)
        await r.delete(test_key)
        
        if value and value.decode() == "ok":
            print("[OK] Redis read/write operations work!")
        
        await r.close()
        return True
        
    except redis.ConnectionError as e:
        print(f"[ERROR] Cannot connect to Redis: {e}")
        print()
        print("Make sure Redis is running:")
        print("  docker run --rm -p 6379:6379 redis:7-alpine")
        print("  OR")
        print("  redis-server")
        return False
        
    except Exception as e:
        print(f"[ERROR] Error: {e}")
        print(f"   Type: {type(e).__name__}")
        return False

if __name__ == "__main__":
    success = asyncio.run(check_redis())
    sys.exit(0 if success else 1)
