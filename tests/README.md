# Test Suite for Dub MVP

This directory contains automated tests that validate the application's functionality, performance, and API stability.

## Test Structure

### Unit Tests
- **`test_performance.py`**: Tests for translation caching and performance optimization
- **`test_redis_backend.py`**: Tests for Redis job store and event bus

### Integration Tests
- **`test_api_integration.py`**: Tests for API endpoints (health, create job, get status)

### Regression Tests
- **`test_api_regression.py`**: Tests to prevent breaking API contract changes

### Load Tests
- **`test_load_performance.py`**: Pytest-based load tests with p95 latency measurement
- **`load_test_standalone.py`**: Standalone script for running load tests independently

## Running Tests

### Prerequisites

1. Install test dependencies:
```bash
pip install pytest pytest-asyncio httpx
```

2. Ensure Redis is running:
```bash
docker run --rm -p 6379:6379 redis:7-alpine
```

3. (Optional) Start the API server for integration/load tests:
```bash
cd dub_mvp
uvicorn app.main:app --reload
```

### Run All Tests

```bash
cd dub_mvp
pytest tests/
```

### Run Specific Test Categories

```bash
# Unit tests only
pytest tests/test_performance.py tests/test_redis_backend.py

# Integration tests only
pytest tests/test_api_integration.py

# Regression tests only
pytest tests/test_api_regression.py

# Load tests only
pytest tests/test_load_performance.py
```

### Run with Verbose Output

```bash
pytest tests/ -v
```

### Run with Coverage

```bash
pytest tests/ --cov=app --cov-report=html
```

## Load Testing

### Using pytest (for CI/CD)

```bash
pytest tests/test_load_performance.py -v
```

### Using Standalone Script

```bash
# Test health endpoint
python tests/load_test_standalone.py --endpoint health --concurrency 10 --requests 5

# Test create dub endpoint (requires audio file)
python tests/load_test_standalone.py \
  --endpoint create_dub \
  --concurrency 5 \
  --requests 3 \
  --audio-file ../harvard.wav

# Test both endpoints
python tests/load_test_standalone.py --endpoint both --concurrency 10 --requests 5
```

### Load Test Output

The load tests measure and report:
- **p50 (median) latency**: Middle value
- **p95 latency**: 95th percentile (key metric for resume claim)
- **p99 latency**: 99th percentile
- **Success rate**: Percentage of successful requests
- **Mean/Min/Max latency**: Additional statistics

Example output:
```
LOAD TEST RESULTS - Health Endpoint
============================================================
Configuration:
  Concurrency: 10
  Requests per worker: 5
  Total requests: 50
  Total time: 2.34s

Results:
  Success rate: 100.00%
  Errors: 0

Latency (ms):
  Min: 12.45
  p50 (median): 18.23
  p95: 45.67
  p99: 52.12
  Max: 55.89
  Mean: 20.45
============================================================
```

## Test Configuration

Tests use a separate Redis database (DB 1) to avoid interfering with production data. This is configured in `conftest.py`:

```python
os.environ["REDIS_URL"] = "redis://localhost:6379/1"
```

Tests automatically clean up after themselves, flushing the test database.

## Continuous Integration

These tests are designed to be run in CI/CD pipelines:

```yaml
# Example GitHub Actions workflow
- name: Run tests
  run: |
    pip install -r requirements.txt
    pytest tests/ -v
```

## Resume Alignment

These tests directly support the resume claims:

✅ **Automated unit/integration tests (pytest)**: `test_performance.py`, `test_redis_backend.py`, `test_api_integration.py`

✅ **API regression cases**: `test_api_regression.py` ensures API contracts don't break

✅ **Repeatable load tests (concurrency + p95 latency)**: `test_load_performance.py` and `load_test_standalone.py` measure p95 latency under defined concurrency

## Troubleshooting

### Tests fail with "Redis connection refused"
- Ensure Redis is running: `docker run --rm -p 6379:6379 redis:7-alpine`

### Integration tests fail with "Connection refused"
- Start the API server: `uvicorn app.main:app --reload`

### Load tests timeout
- Increase timeout in test files or reduce concurrency/requests
- Ensure API server has sufficient resources
