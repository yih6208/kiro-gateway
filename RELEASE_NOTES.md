# Release Notes

## v2.2.1 (2026-02-03)

**Git Commit:** `e5284b3`
**Docker Image:** `ghcr.io/yih6208/kiro-gateway:2.2.1`

### Changes

#### Features
- **Global Rate Limiter** (`df2de5c`)
  - FIFO queue-based rate limiting to prevent 429 errors from Kiro API
  - Configurable via environment variables:
    - `RATE_LIMIT_MAX_CONCURRENT`: Maximum concurrent requests
    - `RATE_LIMIT_MIN_INTERVAL`: Minimum interval between requests (seconds)
    - `RATE_LIMIT_429_BACKOFF`: Global backoff time when 429 received (seconds)

- **Connection Pool Optimization** (`df2de5c`)
  - Configurable HTTP connection pool via environment variables:
    - `HTTP_MAX_CONNECTIONS`: Maximum total connections (default: 100)
    - `HTTP_MAX_KEEPALIVE_CONNECTIONS`: Maximum keep-alive connections (default: 20)
    - `HTTP_KEEPALIVE_EXPIRY`: Keep-alive expiry time in seconds (default: 30)
    - `HTTP_POOL_TIMEOUT`: Pool timeout in seconds (default: 30, use "none" for unlimited)

- **Model List Logging** (`9eac8e8`)
  - Display available models from Kiro API at startup

#### Fixes
- Remove deprecated `_warn_deprecated_debug_setting` import (`9eac8e8`)

### Based On
- Upstream: jwadow/kiro-gateway v2.2 (`73715b4`)
- Includes all upstream features:
  - Network error classification with user-friendly messages
  - Truncation recovery system
  - Model alias system for Cursor IDE compatibility
  - Docker containerization with CI/CD

---

## Version History

| Version | Date | Git Commit | Notes |
|---------|------|------------|-------|
| 2.2.1 | 2026-02-03 | `e5284b3` | Rate limiter, connection pool optimization |
