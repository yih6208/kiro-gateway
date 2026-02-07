# Release Notes

## v2.3.6 (2026-02-06)

**Git Commit:** `68d68ee`
**Docker Image:** `ghcr.io/yih6208/kiro-gateway:2.3.6`

### Changes

#### Bug Fixes
- **Use payload estimate as fallback for accurate auto-compact** (`68d68ee`)
  - When `contextUsageEvent` is missing (e.g., claude-opus-4.6), use pre-request payload token estimate for `input_tokens`
  - Previously fell back to tiktoken estimate which severely underestimated tokens
  - Ensures Claude Code auto-compact triggers correctly at 95% context usage
  - Applied to both streaming and non-streaming Anthropic paths

---

## v2.3.5 (2026-02-06)

**Git Commit:** `561fc49`
**Docker Image:** `ghcr.io/yih6208/kiro-gateway:2.3.5`

### Changes

#### Bug Fixes
- **Fix false truncation detection for claude-opus-4.6** (`561fc49`)
  - Kiro API does not send `contextUsageEvent` for Opus 4.6
  - Previously this caused every Opus 4.6 response to be flagged as truncated
  - Now considers stream complete if any response content was received
  - Applied to both Anthropic and OpenAI streaming paths

#### Improvements
- **Improved token estimation accuracy** (`561fc49`)
  - Simplified estimation by serializing full Kiro payload instead of counting parts separately
  - Added configurable `TOKEN_ESTIMATE_CORRECTION` factor (default 0.95, env var)
  - Added estimated vs actual token comparison in stream summary log
  - Removed verbose parser debug logs to reduce log noise

---

## v2.3.4 (2026-02-06)

**Git Commit:** `69caf7f`
**Docker Image:** `ghcr.io/yih6208/kiro-gateway:2.3.4`

### Changes

#### Bug Fixes
- **Remove claude-sonnet-4.5-1m mapping due to misleading context limits** (`791b973`)
  - Removed HIDDEN_MODELS mapping: `"claude-sonnet-4.5"` -> `"claude-sonnet-4.5-1m"`
  - Testing confirmed that `claude-sonnet-4.5-1m` has the same 200K actual limit as `claude-sonnet-4.5`
  - The 1M metadata from Kiro API is misleading — both models fail at ~198K tokens
  - The mapping caused confusing usage percentages (19% shown when actually at 98%)

#### Improvements
- **Pre-request token estimation from actual Kiro payload** (`69caf7f`)
  - Added token estimation that reads from the converted Kiro payload (not raw Anthropic messages)
  - Includes all injected content: tool documentation, thinking tags, history
  - Logs estimated token count, max tokens, and usage percentage before sending request
  - Warns when estimated tokens exceed model's max input tokens

---

## v2.3.3 (2026-02-06)

**Git Commit:** `c75dfcf`
**Docker Image:** `ghcr.io/yih6208/kiro-gateway:2.3.3`

### Changes

#### Features
- **Claude Opus 4.6 Support** (`c75dfcf`)
  - Added Claude Opus 4.6 model with 16K max output tokens
  - Updated all language documentation (8 languages) with Opus 4.6 announcement
  - Added to FALLBACK_MODELS for DNS/network failure resilience
  - Test fixtures updated with proper token limits (200K input, 16K output)

#### Bug Fixes
- **Fix token calculation for Claude Sonnet 4.5 (1M context)** (`c75dfcf`)
  - **Critical Fix**: Token calculation now correctly uses 1M context limit instead of 200K
  - Root cause: Streaming functions used original request model ID instead of converted model ID
  - Solution: Extract converted model ID from kiro_payload and pass to streaming functions
  - Impact: Large conversations (257+ messages) no longer fail with "Input is too long" error
  - Before: `[Token Calculation] max=200000`
  - After: `[Token Calculation] max=1000000`

#### Improvements
- **Enhanced model logging at startup** (`c75dfcf`)
  - Added detailed token limits logging for all models from Kiro API
  - Shows maxInputTokens and maxOutputTokens for each model
  - Added error handling for None model entries
  - Helps diagnose model availability and token limit issues

#### Technical Details
- Modified `routes_anthropic.py` to extract converted model ID from payload
- Pass `converted_model_id` to `stream_kiro_to_anthropic` and `collect_anthropic_response`
- Added debug logs: "Converted model ID for token calculation" and "[Streaming/Non-streaming] Using model ID"
- Updated `main.py` with try-except for robust model logging

---

## v2.3.2 (2026-02-05)

**Git Commit:** `9beb847`
**Docker Image:** `ghcr.io/yih6208/kiro-gateway:2.3.2`

### Changes

#### Bug Fixes
- **Fix Claude Code auto-compact not triggering** (`9beb847`)
  - Added `input_tokens` to Anthropic `message_delta` event
  - Root cause: Claude Code needs accurate input_tokens from context_usage_percentage to determine when to trigger auto-compaction
  - The initial input_tokens in message_start was estimated from tiktoken (less accurate), now we send the recalculated value from Kiro API's percentage in message_delta

- **Consistent fallback token counting** (`9beb847`)
  - Apply 1.15x Claude correction coefficient in fallback token counting for consistency
  - Added debug logging for context_usage_percentage tracking

---

## v2.3.1 (2026-02-05)

**Git Commit:** `308ef5e`
**Docker Image:** `ghcr.io/yih6208/kiro-gateway:2.3.1`

### Changes

#### Documentation
- **Docker Image Release Workflow** (`308ef5e`)
  - Added comprehensive Docker image release workflow to CLAUDE.md
  - Documented step-by-step process for building, tagging, and pushing images
  - Added version numbering guidelines and release notes format

- **Release Notes Correction** (`a8cc58f`)
  - Fixed commit hash reference in v2.2.1 release notes

#### Upstream Sync
- **Rebased on upstream v2.3** (`58e8129`)
  - Synced with jwadow/kiro-gateway v2.3
  - Includes refactor: use "(empty)" instead of "." for synthetic user message
  - Includes Codex App support and unknown roles handling improvements

### Based On
- Upstream: jwadow/kiro-gateway v2.3 (`58e8129`)
- Includes all upstream features from v2.3:
  - Improved message handling with "(empty)" for synthetic messages
  - Codex App added to supported clients list
  - Complete fix for unknown roles with alternating support

---

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
| 2.3.6 | 2026-02-06 | `68d68ee` | Payload estimate fallback for auto-compact accuracy |
| 2.3.5 | 2026-02-06 | `561fc49` | Fix Opus 4.6 false truncation, improved token estimation |
| 2.3.4 | 2026-02-06 | `69caf7f` | Remove misleading sonnet 4.5-1m mapping, add pre-request token estimation |
| 2.3.3 | 2026-02-06 | `c75dfcf` | Claude Opus 4.6 support, Sonnet 4.5 1M token calculation fix |
| 2.3.2 | 2026-02-05 | `9beb847` | Fix Claude Code auto-compact token reporting |
| 2.3.1 | 2026-02-05 | `308ef5e` | Upstream sync to v2.3, documentation improvements |
| 2.2.1 | 2026-02-03 | `e5284b3` | Rate limiter, connection pool optimization |
