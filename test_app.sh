#!/bin/bash
# Test script for Dub MVP API
# Usage: ./test_app.sh [BASE_URL]

BASE_URL="${1:-http://127.0.0.1:8000}"

echo "Testing Dub MVP API at $BASE_URL"
echo "=================================="
echo ""

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test 1: Health check
echo "1. Testing health endpoint..."
HEALTH=$(curl -s "$BASE_URL/health")
if [[ "$HEALTH" == *"healthy"* ]]; then
    echo -e "${GREEN}[OK] Health check passed${NC}"
    echo "   Response: $HEALTH"
else
    echo -e "${RED}[ERROR] Health check failed${NC}"
    echo "   Response: $HEALTH"
fi
echo ""

# Test 2: Root endpoint
echo "2. Testing root endpoint..."
ROOT=$(curl -s "$BASE_URL/")
if [[ "$ROOT" == *"Dub MVP"* ]]; then
    echo -e "${GREEN}[OK] Root endpoint works${NC}"
    echo "   Response: $ROOT"
else
    echo -e "${RED}[ERROR] Root endpoint failed${NC}"
    echo "   Response: $ROOT"
fi
echo ""

# Test 3: Check if Redis is accessible (via job creation)
echo "3. Testing Redis connection (create job without file)..."
echo "   (This will fail, but should show Redis connection status)"
echo ""

# Test 4: Create a test job (requires a file)
echo "4. To test job creation, run:"
echo -e "${YELLOW}   curl -F \"file=@your_audio_file.wav\" \"$BASE_URL/v1/dubs?src_lang=en&tgt_lang=es&voice=Joanna\"${NC}"
echo ""

# Test 5: Check job status (requires a job_id)
echo "5. To test job status, run:"
echo -e "${YELLOW}   curl \"$BASE_URL/v1/dubs/YOUR_JOB_ID\"${NC}"
echo ""

# Test 6: Stream events (requires a job_id)
echo "6. To test SSE events, run:"
echo -e "${YELLOW}   curl -N \"$BASE_URL/v1/dubs/YOUR_JOB_ID/events\"${NC}"
echo ""

echo "=================================="
echo "Full workflow test:"
echo ""
echo "1. Start Redis: docker run --rm -p 6379:6379 redis:7-alpine"
echo "2. Start API: cd dub_mvp && uvicorn app.main:app --reload"
echo "3. Start Worker: cd dub_mvp && python -m app.worker"
echo "4. Create job: curl -F \"file=@test.wav\" \"$BASE_URL/v1/dubs?src_lang=en&tgt_lang=es\""
echo "5. Stream events: curl -N \"$BASE_URL/v1/dubs/JOB_ID/events\""
echo "6. Get result: curl -o result.wav \"$BASE_URL/v1/dubs/JOB_ID/result\""
echo ""
