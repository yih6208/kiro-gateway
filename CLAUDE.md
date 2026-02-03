# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Kiro Gateway is a proxy server that provides OpenAI-compatible and Anthropic-compatible APIs for the Kiro API (Amazon Q Developer / AWS CodeWhisperer). It translates requests between different API formats and handles authentication, streaming, and model resolution.

## Development Commands

### Setup
```bash
# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your credentials
```

### Running the Server
```bash
# Default (host: 0.0.0.0, port: 8000)
python main.py

# Custom port
python main.py --port 9000

# Custom host and port
python main.py --host 127.0.0.1 --port 9000

# Using uvicorn directly
uvicorn main:app --host 0.0.0.0 --port 8000
```

### Testing
```bash
# Run all tests
pytest

# Run specific test file
pytest tests/unit/test_tokenizer.py

# Run with verbose output
pytest -v

# Run integration tests only
pytest tests/integration/

# Run unit tests only
pytest tests/unit/
```

### Manual API Testing
```bash
# Manual testing script (not run by pytest)
python manual_api_test.py
```

## Architecture Overview

### Request Flow

1. **Client Request** → FastAPI endpoint (`routes_openai.py` or `routes_anthropic.py`)
2. **Authentication** → `verify_api_key()` checks `PROXY_API_KEY`
3. **Format Conversion** → `converters_*.py` converts OpenAI/Anthropic format to Kiro format
4. **Model Resolution** → `model_resolver.py` normalizes model names and resolves to internal IDs
5. **HTTP Request** → `http_client.py` sends request to Kiro API with retry logic
6. **Streaming Response** → `streaming_*.py` converts Kiro SSE stream back to OpenAI/Anthropic format
7. **Client Response** → Streamed or collected response returned to client

### Key Components

#### Authentication System (`kiro/auth.py`)
- **KiroAuthManager**: Manages token lifecycle with automatic refresh
- **Multiple auth types**:
  - Kiro Desktop Auth (default): Uses `refreshToken` from Kiro IDE
  - AWS SSO OIDC: For kiro-cli with AWS IAM Identity Center
- **Credential sources** (priority order):
  1. SQLite database (`KIRO_CLI_DB_FILE`) - for kiro-cli
  2. JSON file (`KIRO_CREDS_FILE`) - for Kiro IDE
  3. Environment variables (`REFRESH_TOKEN`)
- **Token refresh**: Automatic refresh 10 minutes before expiration
- **Graceful degradation**: Falls back to existing access token if refresh fails

#### Format Conversion Pipeline (`kiro/converters_*.py`)
- **converters_core.py**: Shared logic for both OpenAI and Anthropic
  - `UnifiedMessage`: Internal message format
  - `UnifiedTool`: Internal tool format
  - Tool description handling: Long descriptions moved to system prompt
- **converters_openai.py**: OpenAI → Kiro conversion
- **converters_anthropic.py**: Anthropic → Kiro conversion
- **Key challenge**: Kiro API has different format than OpenAI/Anthropic
  - Messages must be merged (consecutive same-role messages)
  - Tool calls and results have different structures
  - System prompts handled differently

#### Model Resolution (`kiro/model_resolver.py`)
4-layer resolution pipeline:
1. **Normalize**: Convert client formats to Kiro format (e.g., `claude-sonnet-4-5` → `claude-sonnet-4.5`)
2. **Dynamic Cache**: Check models from `/ListAvailableModels` API (loaded at startup)
3. **Hidden Models**: Check manually configured models (e.g., `claude-3.7-sonnet`)
4. **Pass-through**: Unknown models sent to Kiro API (let Kiro decide)

Philosophy: "We are a gateway, not a gatekeeper" - unknown models are passed through rather than rejected.

#### Streaming System (`kiro/streaming_*.py`)
- **streaming_core.py**: Core SSE parsing and event handling
- **streaming_openai.py**: Kiro → OpenAI format conversion
- **streaming_anthropic.py**: Kiro → Anthropic format conversion
- **First token timeout**: Cancels and retries if model doesn't respond within `FIRST_TOKEN_TIMEOUT` (default 15s)
- **Thinking parser** (`thinking_parser.py`): Extracts `<thinking>` blocks for extended reasoning

#### Extended Thinking ("Fake Reasoning")
- **Not native API support**: Injects `<thinking_mode>enabled</thinking_mode>` tags into prompts
- **Model responds** with `<thinking>...</thinking>` blocks
- **Parser extracts** thinking content and converts to `reasoning_content` field (OpenAI format)
- **Configurable**: `FAKE_REASONING_ENABLED`, `FAKE_REASONING_HANDLING`, `FAKE_REASONING_MAX_TOKENS`
- Called "fake" because it's a prompt injection hack, not official API support

#### HTTP Client with Retry (`kiro/http_client.py`)
- **Automatic retries** on 403, 429, 5xx errors
- **Token refresh** on 403 (expired token)
- **Exponential backoff**: `BASE_RETRY_DELAY * (2 ** attempt)`
- **Max retries**: `MAX_RETRIES` (default 3)
- **Connection pooling**: Shared `httpx.AsyncClient` in `app.state.http_client`

#### Debug Logging (`kiro/debug_logger.py`, `kiro/debug_middleware.py`)
- **Three modes**: `off`, `errors` (recommended), `all`
- **Middleware**: Captures requests before Pydantic validation (catches 422 errors)
- **Logs saved to**: `debug_logs/` directory
  - `request_body.json`: Original client request
  - `kiro_request_body.json`: Converted Kiro request
  - `response_stream_raw.txt`: Raw Kiro response
  - `response_stream_modified.txt`: Converted response
  - `app_logs.txt`: Application logs
  - `error_info.json`: Error details (on errors)

### Configuration System (`kiro/config.py`)

All settings loaded from `.env` file via `python-dotenv`. Key patterns:
- **Raw path reading**: `_get_raw_env_value()` avoids escape sequence issues on Windows
- **URL templates**: Dynamic URLs based on region (e.g., `KIRO_API_HOST_TEMPLATE`)
- **Validation**: `validate_configuration()` in `main.py` checks required settings at startup
- **Priority**: CLI args > Environment variables > Defaults

### Application Lifecycle (`main.py`)

**Startup (lifespan manager)**:
1. Create shared HTTP client with connection pooling
2. Initialize `KiroAuthManager` with credentials
3. Create `ModelInfoCache`
4. **Blocking**: Load models from Kiro API (ensures cache populated before accepting requests)
5. Add hidden models to cache
6. Initialize `ModelResolver`

**Shutdown**:
1. Close shared HTTP client
2. Clean up resources

### VPN/Proxy Support

- **Environment variables**: `VPN_PROXY_URL` sets `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`
- **Automatic**: `httpx` picks up proxy from environment
- **Protocols**: HTTP, HTTPS, SOCKS5
- **Use case**: China (GFW), corporate networks, privacy

## Important Patterns

### Error Handling
- **HTTPException**: For client errors (400, 401, 404)
- **Retry logic**: For transient errors (403, 429, 5xx)
- **Graceful degradation**: Use stale tokens if refresh fails but token not expired
- **Debug logging**: Flush logs on errors for troubleshooting

### Async/Await
- All I/O operations are async (httpx, FastAPI)
- **Lock**: `asyncio.Lock` in `KiroAuthManager` for thread-safe token refresh
- **Streaming**: Async generators for SSE streaming

### Type Safety
- **Pydantic models**: `models_openai.py`, `models_anthropic.py` for request/response validation
- **Dataclasses**: `UnifiedMessage`, `UnifiedTool` for internal formats
- **Type hints**: Throughout codebase

### Logging
- **Loguru**: Structured logging with colors
- **InterceptHandler**: Redirects uvicorn/FastAPI logs to loguru
- **Levels**: TRACE, DEBUG, INFO, WARNING, ERROR, CRITICAL
- **Filtering**: Suppresses shutdown exceptions (CancelledError, KeyboardInterrupt)

## Testing Strategy

- **Unit tests** (`tests/unit/`): Test individual components in isolation
- **Integration tests** (`tests/integration/`): Test full request flow
- **Fixtures** (`conftest.py`): Shared test fixtures and mocks
- **pytest-asyncio**: For testing async code
- **hypothesis**: Property-based testing (if used)

## Common Development Tasks

### Adding a New Model
1. If model is in Kiro API: No changes needed (auto-discovered at startup)
2. If model is hidden: Add to `HIDDEN_MODELS` in `config.py`

### Adding a New API Endpoint
1. Create route in `routes_openai.py` or `routes_anthropic.py`
2. Add Pydantic models in `models_*.py` if needed
3. Implement converter in `converters_*.py` if new format
4. Add tests in `tests/unit/` or `tests/integration/`

### Debugging Request/Response Issues
1. Enable debug mode: `DEBUG_MODE=errors` in `.env`
2. Make request that fails
3. Check `debug_logs/` directory for detailed logs
4. Review `kiro_request_body.json` to see what was sent to Kiro
5. Review `response_stream_raw.txt` to see what Kiro returned

### Modifying Authentication
- All auth logic in `kiro/auth.py`
- Add new auth type: Create new `AuthType` enum value and implement `_refresh_token_*()` method
- Credential loading: Modify `_load_credentials_from_*()` methods

### Changing Streaming Behavior
- Core streaming: `streaming_core.py`
- Format-specific: `streaming_openai.py` or `streaming_anthropic.py`
- Thinking extraction: `thinking_parser.py`
- First token timeout: `FIRST_TOKEN_TIMEOUT` in config

### Docker Image Release Workflow (IMPORTANT)

When building and releasing a new Docker image, **ALWAYS** follow this workflow:

#### Step 1: Build and Push Docker Image
```bash
# Build image
docker build -f docker/Dockerfile -t kiro-gateway:latest .

# Tag with both latest and version number
docker tag kiro-gateway:latest ghcr.io/yih6208/kiro-gateway:latest
docker tag kiro-gateway:latest ghcr.io/yih6208/kiro-gateway:X.X.X

# Push both tags
docker push ghcr.io/yih6208/kiro-gateway:latest
docker push ghcr.io/yih6208/kiro-gateway:X.X.X
```

#### Step 2: Update Release Notes
1. Get the current git commit hash: `git log --oneline -1`
2. Update `RELEASE_NOTES.md` with:
   - Version number (e.g., `v2.2.1`)
   - Git commit hash
   - Docker image tag
   - List of changes/features
3. **IMPORTANT**: Commit the release notes as a **separate commit** (do NOT amend the previous commit to avoid infinite loop)

#### Step 3: Push to Git
```bash
git add RELEASE_NOTES.md
git commit -m "docs: update RELEASE_NOTES.md for vX.X.X"
git push yih6208 main
```

#### Version Numbering
- Follow semantic versioning: `MAJOR.MINOR.PATCH`
- Base version follows upstream (e.g., `2.2` from jwadow/kiro-gateway)
- Add patch version for fork-specific changes (e.g., `2.2.1`, `2.2.2`)

#### Docker Registry
- **Registry**: `ghcr.io/yih6208/kiro-gateway`
- **Authentication**: Use GitHub Personal Access Token with `write:packages` scope
- **Login**: `echo "TOKEN" | docker login ghcr.io -u yih6208 --password-stdin`

#### Release Notes Format
See `RELEASE_NOTES.md` for the template. Each release should include:
- Git commit hash
- Docker image tag
- List of changes (features, fixes)
- Version history table

## Key Files Reference

- `main.py` - Application entry point and lifecycle
- `kiro/auth.py` - Authentication and token management
- `kiro/config.py` - Configuration and environment variables
- `kiro/routes_openai.py` - OpenAI-compatible endpoints
- `kiro/routes_anthropic.py` - Anthropic-compatible endpoints
- `kiro/converters_core.py` - Shared conversion logic
- `kiro/converters_openai.py` - OpenAI format conversion
- `kiro/converters_anthropic.py` - Anthropic format conversion
- `kiro/streaming_core.py` - Core SSE streaming
- `kiro/streaming_openai.py` - OpenAI streaming conversion
- `kiro/streaming_anthropic.py` - Anthropic streaming conversion
- `kiro/model_resolver.py` - Model name resolution
- `kiro/http_client.py` - HTTP client with retry logic
- `kiro/thinking_parser.py` - Extended thinking extraction
- `kiro/cache.py` - Model information caching
- `kiro/debug_logger.py` - Debug logging system
- `kiro/utils.py` - Utility functions
