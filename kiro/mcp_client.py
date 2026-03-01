# -*- coding: utf-8 -*-

"""
InvokeMCP client for server-side tool execution.

Calls Kiro's InvokeMCP endpoint to execute tools like web_search and web_fetch
server-side, then converts results to Anthropic's server tool response format.
"""

import json
import uuid
from typing import Any, Dict, List, TYPE_CHECKING

import httpx
from loguru import logger

if TYPE_CHECKING:
    from kiro.auth import KiroAuthManager

# Tools that can be executed server-side via InvokeMCP
SERVER_SIDE_TOOLS = {"web_search"}


class MCPToolError(Exception):
    """Raised when an InvokeMCP call fails."""
    pass


def is_server_side_tool(name: str) -> bool:
    """Check if a tool name is a server-side tool."""
    return name in SERVER_SIDE_TOOLS


def generate_server_tool_id() -> str:
    """Generate a server tool use ID with srvtoolu_ prefix."""
    return f"srvtoolu_{uuid.uuid4().hex[:24]}"


async def invoke_mcp_tool(
    auth_manager: "KiroAuthManager",
    tool_name: str,
    arguments: Dict[str, Any],
    timeout: float = 30.0,
) -> Dict[str, Any]:
    """
    Call Kiro's InvokeMCP endpoint to execute a server-side tool.

    Args:
        auth_manager: Auth manager for token + API host
        tool_name: Tool name (e.g. "web_search", "web_fetch")
        arguments: Tool arguments (e.g. {"query": "..."})
        timeout: Request timeout in seconds

    Returns:
        Parsed JSON-RPC result from InvokeMCP

    Raises:
        MCPToolError: On any failure
    """
    token = await auth_manager.get_access_token()
    url = f"{auth_manager.api_host}/"

    headers = {
        "content-type": "application/x-amz-json-1.0",
        "x-amz-target": "AmazonCodeWhispererStreamingService.InvokeMCP",
        "authorization": f"Bearer {token}",
        "x-amzn-codewhisperer-optout": "false",
    }

    body = {
        "id": str(uuid.uuid4()),
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }

    logger.info(f"[InvokeMCP] Calling {tool_name} with args: {json.dumps(arguments)[:200]}")

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=headers, json=body)

        if resp.status_code == 403:
            # Token expired — refresh and retry once
            logger.warning("[InvokeMCP] Got 403, refreshing token and retrying")
            await auth_manager.force_refresh()
            token = await auth_manager.get_access_token()
            headers["authorization"] = f"Bearer {token}"

            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, headers=headers, json=body)

        if resp.status_code != 200:
            raise MCPToolError(
                f"InvokeMCP returned {resp.status_code}: {resp.text[:300]}"
            )

        data = resp.json()

        # Check for JSON-RPC error
        if "error" in data:
            err = data["error"]
            raise MCPToolError(
                f"InvokeMCP JSON-RPC error: {err.get('message', str(err))}"
            )

        logger.debug(f"[InvokeMCP] {tool_name} returned successfully")
        return data.get("result", {})

    except MCPToolError:
        raise
    except Exception as e:
        raise MCPToolError(f"InvokeMCP request failed: {e}") from e


def convert_mcp_to_anthropic_search_results(
    mcp_result: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Convert InvokeMCP web_search result to Anthropic web_search_result format.

    InvokeMCP returns:
        {"content": [{"type": "text", "text": "{\"results\":[...]}"}]}

    Each result has: title, url, snippet, publishedDate, domain, id

    Anthropic expects:
        [{"type": "web_search_result", "url": "...", "title": "...",
          "encrypted_content": "...", "page_age": "..."}]
    """
    content_list = mcp_result.get("content", [])
    if not content_list:
        return []

    # The first content block contains JSON-encoded results
    text_block = content_list[0]
    raw_text = text_block.get("text", "")

    try:
        parsed = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        logger.warning(f"[InvokeMCP] Failed to parse search results JSON: {raw_text[:200]}")
        return []

    results = parsed.get("results", [])
    anthropic_results = []

    for r in results:
        item = {
            "type": "web_search_result",
            "url": r.get("url", ""),
            "title": r.get("title", ""),
            "content": r.get("snippet", ""),
        }
        # Convert publishedDate to page_age if present
        published = r.get("publishedDate")
        if published:
            item["page_age"] = str(published)

        anthropic_results.append(item)

    logger.debug(f"[InvokeMCP] Converted {len(anthropic_results)} search results to Anthropic format")
    return anthropic_results


