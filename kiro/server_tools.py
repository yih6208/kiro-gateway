# -*- coding: utf-8 -*-

"""
Server-side tool execution for Anthropic streaming and non-streaming.

When Kiro's model emits tool_use events for server-side tools (web_search),
this module intercepts them, executes via InvokeMCP, sends results back to
Kiro as a continuation, and streams the model's final answer to the client.

All Anthropic blocks are preserved: server_tool_use, web_search_tool_result,
and the final text response from the model.

Streaming flow:
1. Stream text/thinking from Kiro to client
2. Collect tool_use events silently
3. If server-side tools found:
   a. Emit server_tool_use + web_search_tool_result blocks to client
   b. Build continuation payload with results, send back to Kiro
   c. Stream Kiro's second response (final answer) to client
4. If client-side tools: emit tool_use blocks normally
5. Emit message_delta + message_stop

Non-streaming flow: same logic, collected instead of streamed.
"""

import copy
import json
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional, TYPE_CHECKING

import httpx
from loguru import logger

from kiro.mcp_client import (
    is_server_side_tool,
    invoke_mcp_tool,
    convert_mcp_to_anthropic_search_results,
    generate_server_tool_id,
    MCPToolError,
)
from kiro.streaming_anthropic import format_sse_event, generate_message_id, generate_thinking_signature, extract_tool_fields
from kiro.streaming_core import parse_kiro_stream, collect_stream_to_result, calculate_tokens_from_context_usage
from kiro.tokenizer import count_tokens, count_message_tokens
from kiro.config import FIRST_TOKEN_TIMEOUT, FAKE_REASONING_HANDLING

if TYPE_CHECKING:
    from kiro.auth import KiroAuthManager
    from kiro.cache import ModelInfoCache
    from kiro.http_client import KiroHttpClient

MAX_SERVER_TOOL_LOOPS = 5


def _get_tool_name(tc: Dict[str, Any]) -> str:
    """Extract tool name from a Kiro tool call dict."""
    return tc.get("function", {}).get("name", "") or tc.get("name", "")


def _build_tool_use_content_block(tc: Dict[str, Any]) -> Dict[str, Any]:
    """Build an Anthropic tool_use content block from a Kiro tool call."""
    tool_id, tool_name, tool_input = extract_tool_fields(tc)
    return {"type": "tool_use", "id": tool_id, "name": tool_name, "input": tool_input}


async def _execute_server_tool(
    auth_manager: "KiroAuthManager",
    name: str,
    args: Dict[str, Any],
) -> tuple:
    """
    Execute a server-side tool via InvokeMCP.

    Returns (search_results, result_text) where search_results is None on error.
    """
    search_results = None
    result_text = ""
    try:
        mcp_result = await invoke_mcp_tool(auth_manager, name, args)
        search_results = convert_mcp_to_anthropic_search_results(mcp_result)
        result_text = _format_search_results_as_text(search_results)
    except MCPToolError as e:
        logger.error(f"[InvokeMCP] {name} failed: {e}")
        result_text = f"Error: web search unavailable ({e})"
    return search_results, result_text


def _build_search_result_block(server_tool_id: str, search_results) -> Dict[str, Any]:
    """Build a web_search_tool_result content block."""
    if search_results:
        return {
            "type": "web_search_tool_result",
            "tool_use_id": server_tool_id,
            "content": search_results,
        }
    return {
        "type": "web_search_tool_result",
        "tool_use_id": server_tool_id,
        "content": [{"type": "web_search_tool_result_error", "error_code": "unavailable"}],
    }


def _format_search_results_as_text(search_results: List[Dict[str, Any]]) -> str:
    """Format search results as plain text for the model to read."""
    lines = ["Web search results:\n"]
    for i, r in enumerate(search_results, 1):
        title = r.get("title", "")
        url = r.get("url", "")
        content = r.get("content", "") or r.get("encrypted_content", "")
        lines.append(f"{i}. [{title}]({url})")
        if content:
            lines.append(f"   {content}")
    return "\n".join(lines)


async def stream_with_server_tool_loop(
    response: httpx.Response,
    auth_manager: "KiroAuthManager",
    http_client: "KiroHttpClient",
    model: str,
    model_cache: "ModelInfoCache",
    kiro_payload: Dict[str, Any],
    first_token_timeout: float = FIRST_TOKEN_TIMEOUT,
    request_messages: Optional[list] = None,
    estimated_input_tokens: Optional[int] = None,
    usage_tracker=None,
    api_key_id: Optional[str] = None,
    kiro_account_id: Optional[int] = None,
) -> AsyncGenerator[str, None]:
    """
    Stream Kiro response with server-side tool execution loop.

    Flow:
    1. Stream text/thinking to client, collect tool_use silently
    2. If server-side tools found:
       a. Emit server_tool_use + web_search_tool_result blocks to client
       b. Build continuation payload with results, send back to Kiro
       c. Stream Kiro's second response (final answer) to client
    3. If client-side tools: emit tool_use blocks normally
    4. Emit message_delta + message_stop
    """
    message_id = generate_message_id()
    block_index = 0
    total_input_tokens = 0
    total_output_tokens = 0
    is_first_turn = True
    web_search_count = 0

    if request_messages:
        total_input_tokens = count_message_tokens(request_messages, apply_claude_correction=False)

    # Emit message_start only once
    yield format_sse_event("message_start", {
        "type": "message_start",
        "message": {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": model,
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {
                "input_tokens": total_input_tokens,
                "output_tokens": 0,
            },
        },
    })

    current_response = response
    loop_count = 0

    try:
        while loop_count < MAX_SERVER_TOOL_LOOPS:
            loop_count += 1

            # Stream one turn — text/thinking goes to client, tool_use collected silently
            turn_result = _TurnResult()
            async for sse_chunk in _stream_single_turn(
                current_response, model, model_cache, auth_manager,
                block_index, turn_result, first_token_timeout,
                estimated_input_tokens if is_first_turn else None,
            ):
                yield sse_chunk

            block_index = turn_result.next_block_index
            total_output_tokens += turn_result.output_tokens

            if turn_result.context_usage_percentage is not None:
                prompt_tokens, _, _, _ = calculate_tokens_from_context_usage(
                    turn_result.context_usage_percentage,
                    total_output_tokens,
                    model_cache,
                    model,
                )
                total_input_tokens = prompt_tokens
            elif estimated_input_tokens is not None and is_first_turn:
                total_input_tokens = estimated_input_tokens

            is_first_turn = False

            # No tool calls — done
            if not turn_result.tool_calls:
                break

            # Partition tool calls
            server_tools = []
            client_tools = []
            for tc in turn_result.tool_calls:
                if is_server_side_tool(_get_tool_name(tc)):
                    server_tools.append(tc)
                else:
                    client_tools.append(tc)

            if not server_tools:
                # All client-side — emit tool_use blocks normally
                for tc in client_tools:
                    async for chunk in _emit_tool_use_block(tc, block_index):
                        yield chunk
                    block_index += 1
                break

            # Execute server-side tools and emit blocks to client
            server_tool_results = []
            for tc in server_tools:
                _, name, args = extract_tool_fields(tc)

                if name == "web_search":
                    web_search_count += 1

                server_tool_id = generate_server_tool_id()

                # Emit server_tool_use block to client
                yield format_sse_event("content_block_start", {
                    "type": "content_block_start",
                    "index": block_index,
                    "content_block": {
                        "type": "server_tool_use",
                        "id": server_tool_id,
                        "name": name,
                        "input": {},
                    },
                })
                yield format_sse_event("content_block_delta", {
                    "type": "content_block_delta",
                    "index": block_index,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": json.dumps(args, ensure_ascii=False),
                    },
                })
                yield format_sse_event("content_block_stop", {
                    "type": "content_block_stop",
                    "index": block_index,
                })
                block_index += 1

                # Execute via InvokeMCP
                search_results, result_text = await _execute_server_tool(auth_manager, name, args)

                # Emit web_search_tool_result block to client
                yield format_sse_event("content_block_start", {
                    "type": "content_block_start",
                    "index": block_index,
                    "content_block": _build_search_result_block(server_tool_id, search_results),
                })
                yield format_sse_event("content_block_stop", {
                    "type": "content_block_stop",
                    "index": block_index,
                })
                block_index += 1

                server_tool_results.append({
                    "tool_use_id": tc.get("id", ""),
                    "name": name,
                    "content": result_text,
                })

            if client_tools:
                # Mixed: server tools done, emit client-side tool_use blocks and stop
                for tc in client_tools:
                    async for chunk in _emit_tool_use_block(tc, block_index):
                        yield chunk
                    block_index += 1
                break

            # All server-side — build continuation and loop back to Kiro
            logger.info(f"[ServerTools] Executed {len(server_tools)} server-side tool(s), building continuation (loop {loop_count})")

            try:
                continuation_payload = _build_continuation_payload(
                    kiro_payload,
                    turn_result.full_content,
                    turn_result.tool_calls,
                    server_tool_results,
                )

                url = f"{auth_manager.api_host}/generateAssistantResponse"
                current_response = await http_client.request_with_retry(
                    "POST", url, continuation_payload, stream=True
                )

                if current_response.status_code != 200:
                    error_text = (await current_response.aread()).decode("utf-8", errors="replace")
                    logger.error(f"[ServerTools] Continuation failed: {current_response.status_code} - {error_text[:200]}")
                    break

            except Exception as e:
                logger.error(f"[ServerTools] Continuation request failed: {e}")
                break

        if loop_count >= MAX_SERVER_TOOL_LOOPS:
            logger.warning(f"[ServerTools] Max loops ({MAX_SERVER_TOOL_LOOPS}) exceeded")

        # Determine stop reason
        has_client_tools = any(
            not is_server_side_tool(_get_tool_name(tc))
            for tc in (turn_result.tool_calls or [])
        ) if turn_result.tool_calls else False

        stop_reason = "tool_use" if has_client_tools else "end_turn"

        # Build usage
        usage = {
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
        }
        if web_search_count > 0:
            usage["server_tool_use"] = {
                "web_search_requests": web_search_count,
            }

        # Emit message_delta + message_stop
        yield format_sse_event("message_delta", {
            "type": "message_delta",
            "delta": {
                "stop_reason": stop_reason,
                "stop_sequence": None,
            },
            "usage": usage,
        })

        yield format_sse_event("message_stop", {"type": "message_stop"})

        # Record usage
        if usage_tracker and api_key_id and kiro_account_id:
            try:
                await usage_tracker.record_request(
                    api_key_id=api_key_id,
                    kiro_account_id=kiro_account_id,
                    model=model,
                    endpoint="/v1/messages",
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    status_code=200,
                    duration_ms=0,
                )
                await usage_tracker.flush()
            except Exception as e:
                logger.error(f"Failed to record usage: {e}")

    except GeneratorExit:
        logger.debug("[ServerTools] Client disconnected (GeneratorExit)")
        raise
    except Exception as e:
        logger.error(f"[ServerTools] Error: {e}", exc_info=True)
        yield format_sse_event("error", {
            "type": "error",
            "error": {"type": "api_error", "message": f"Internal error: {e}"},
        })
        raise
    finally:
        try:
            await current_response.aclose()
        except Exception:
            pass


class _TurnResult:
    """Accumulates results from a single Kiro response turn."""

    def __init__(self):
        self.full_content: str = ""
        self.full_thinking: str = ""
        self.tool_calls: List[Dict[str, Any]] = []
        self.output_tokens: int = 0
        self.context_usage_percentage: Optional[float] = None
        self.next_block_index: int = 0


async def _stream_single_turn(
    response: httpx.Response,
    model: str,
    model_cache: "ModelInfoCache",
    auth_manager: "KiroAuthManager",
    start_block_index: int,
    turn_result: _TurnResult,
    first_token_timeout: float,
    estimated_input_tokens: Optional[int],
) -> AsyncGenerator[str, None]:
    """
    Stream one Kiro response turn.

    Emits text/thinking SSE events to client, collects tool_use events
    into turn_result (does NOT emit them — caller decides what to do).
    Does NOT emit message_delta/message_stop.
    """
    block_index = start_block_index
    thinking_block_started = False
    thinking_block_index: Optional[int] = None
    text_block_started = False
    text_block_index: Optional[int] = None
    thinking_signature = generate_thinking_signature()

    from kiro.parsers import parse_bracket_tool_calls

    async for event in parse_kiro_stream(response, first_token_timeout):
        if event.type == "content":
            content = event.content or ""
            turn_result.full_content += content

            # Close thinking block if transitioning to content
            if thinking_block_started and thinking_block_index is not None:
                yield format_sse_event("content_block_stop", {
                    "type": "content_block_stop",
                    "index": thinking_block_index,
                })
                thinking_block_started = False
                block_index += 1

            if not text_block_started:
                text_block_index = block_index
                yield format_sse_event("content_block_start", {
                    "type": "content_block_start",
                    "index": text_block_index,
                    "content_block": {"type": "text", "text": ""},
                })
                text_block_started = True

            if content:
                yield format_sse_event("content_block_delta", {
                    "type": "content_block_delta",
                    "index": text_block_index,
                    "delta": {"type": "text_delta", "text": content},
                })

        elif event.type == "thinking":
            thinking_content = event.thinking_content or ""
            turn_result.full_thinking += thinking_content

            if FAKE_REASONING_HANDLING == "as_reasoning_content":
                if not thinking_block_started:
                    thinking_block_index = block_index
                    yield format_sse_event("content_block_start", {
                        "type": "content_block_start",
                        "index": thinking_block_index,
                        "content_block": {
                            "type": "thinking",
                            "thinking": "",
                            "signature": thinking_signature,
                        },
                    })
                    thinking_block_started = True

                if thinking_content:
                    yield format_sse_event("content_block_delta", {
                        "type": "content_block_delta",
                        "index": thinking_block_index,
                        "delta": {"type": "thinking_delta", "thinking": thinking_content},
                    })

        elif event.type == "tool_use" and event.tool_use:
            # Close open blocks before collecting tool
            if thinking_block_started and thinking_block_index is not None:
                yield format_sse_event("content_block_stop", {
                    "type": "content_block_stop",
                    "index": thinking_block_index,
                })
                thinking_block_started = False
                block_index += 1

            if text_block_started and text_block_index is not None:
                yield format_sse_event("content_block_stop", {
                    "type": "content_block_stop",
                    "index": text_block_index,
                })
                text_block_started = False
                block_index += 1

            turn_result.tool_calls.append(event.tool_use)

        elif event.type == "context_usage" and event.context_usage_percentage is not None:
            turn_result.context_usage_percentage = event.context_usage_percentage

    # Check bracket-style tool calls
    bracket_tools = parse_bracket_tool_calls(turn_result.full_content)
    if bracket_tools:
        # Close open blocks
        if thinking_block_started and thinking_block_index is not None:
            yield format_sse_event("content_block_stop", {
                "type": "content_block_stop",
                "index": thinking_block_index,
            })
            thinking_block_started = False
            block_index += 1
        if text_block_started and text_block_index is not None:
            yield format_sse_event("content_block_stop", {
                "type": "content_block_stop",
                "index": text_block_index,
            })
            text_block_started = False
            block_index += 1

        turn_result.tool_calls.extend(bracket_tools)

    # Close remaining open blocks
    if thinking_block_started and thinking_block_index is not None:
        yield format_sse_event("content_block_stop", {
            "type": "content_block_stop",
            "index": thinking_block_index,
        })
        block_index += 1

    if text_block_started and text_block_index is not None:
        yield format_sse_event("content_block_stop", {
            "type": "content_block_stop",
            "index": text_block_index,
        })
        block_index += 1

    turn_result.output_tokens = count_tokens(
        turn_result.full_content + turn_result.full_thinking
    )
    turn_result.next_block_index = block_index


async def _emit_tool_use_block(
    tc: Dict[str, Any], block_index: int
) -> AsyncGenerator[str, None]:
    """Emit a standard tool_use block (for client-side tools)."""
    tool_id, tool_name, tool_input = extract_tool_fields(tc)

    yield format_sse_event("content_block_start", {
        "type": "content_block_start",
        "index": block_index,
        "content_block": {
            "type": "tool_use",
            "id": tool_id,
            "name": tool_name,
            "input": {},
        },
    })

    yield format_sse_event("content_block_delta", {
        "type": "content_block_delta",
        "index": block_index,
        "delta": {
            "type": "input_json_delta",
            "partial_json": json.dumps(tool_input, ensure_ascii=False),
        },
    })

    yield format_sse_event("content_block_stop", {
        "type": "content_block_stop",
        "index": block_index,
    })


async def collect_with_server_tools(
    response: httpx.Response,
    auth_manager: "KiroAuthManager",
    http_client: "KiroHttpClient",
    model: str,
    model_cache: "ModelInfoCache",
    kiro_payload: Dict[str, Any],
    request_messages: Optional[list] = None,
    estimated_input_tokens: Optional[int] = None,
    usage_tracker=None,
    api_key_id: Optional[str] = None,
    kiro_account_id: Optional[int] = None,
) -> dict:
    """
    Collect Kiro response, execute server-side tools, send continuation,
    and return the model's final answer with all Anthropic blocks preserved.
    """
    message_id = generate_message_id()

    input_tokens = 0
    if request_messages:
        input_tokens = count_message_tokens(request_messages, apply_claude_correction=False)

    current_response = response
    total_output_tokens = 0
    content_blocks = []
    web_search_count = 0
    context_usage_pct = None
    has_client_tools = False

    for loop_count in range(1, MAX_SERVER_TOOL_LOOPS + 1):
        result = await collect_stream_to_result(current_response)
        total_output_tokens += count_tokens(result.content + result.thinking_content)

        if result.context_usage_percentage is not None:
            context_usage_pct = result.context_usage_percentage

        # Add thinking block
        if result.thinking_content and FAKE_REASONING_HANDLING == "as_reasoning_content":
            content_blocks.append({
                "type": "thinking",
                "thinking": result.thinking_content,
                "signature": generate_thinking_signature(),
            })

        # Add text block
        text_content = result.content
        if result.thinking_content and FAKE_REASONING_HANDLING == "include_as_text":
            text_content = result.thinking_content + text_content
        if text_content:
            content_blocks.append({"type": "text", "text": text_content})

        # No tool calls — done
        if not result.tool_calls:
            break

        # Partition
        server_tools = []
        client_tools = []
        for tc in result.tool_calls:
            if is_server_side_tool(_get_tool_name(tc)):
                server_tools.append(tc)
            else:
                client_tools.append(tc)

        if not server_tools:
            # All client-side
            for tc in client_tools:
                content_blocks.append(_build_tool_use_content_block(tc))
            has_client_tools = True
            break

        # Execute server-side tools
        server_tool_results = []
        for tc in server_tools:
            _, name, args = extract_tool_fields(tc)

            if name == "web_search":
                web_search_count += 1

            server_tool_id = generate_server_tool_id()

            content_blocks.append({
                "type": "server_tool_use",
                "id": server_tool_id,
                "name": name,
                "input": args,
            })

            search_results, result_text = await _execute_server_tool(auth_manager, name, args)
            content_blocks.append(_build_search_result_block(server_tool_id, search_results))

            server_tool_results.append({
                "tool_use_id": tc.get("id", ""),
                "name": name,
                "content": result_text,
            })

        if client_tools:
            for tc in client_tools:
                content_blocks.append(_build_tool_use_content_block(tc))
            has_client_tools = True
            break

        # Build continuation
        logger.info(f"[ServerTools] Non-streaming continuation (loop {loop_count})")
        try:
            continuation_payload = _build_continuation_payload(
                kiro_payload, result.content, result.tool_calls, server_tool_results,
            )
            url = f"{auth_manager.api_host}/generateAssistantResponse"
            current_response = await http_client.request_with_retry(
                "POST", url, continuation_payload, stream=True
            )
            if current_response.status_code != 200:
                error_text = (await current_response.aread()).decode("utf-8", errors="replace")
                logger.error(f"[ServerTools] Continuation failed: {current_response.status_code} - {error_text[:200]}")
                break
        except Exception as e:
            logger.error(f"[ServerTools] Continuation request failed: {e}")
            break

    # Calculate tokens
    if context_usage_pct is not None:
        prompt_tokens, _, _, _ = calculate_tokens_from_context_usage(
            context_usage_pct, total_output_tokens, model_cache, model
        )
        input_tokens = prompt_tokens
    elif estimated_input_tokens is not None:
        input_tokens = estimated_input_tokens

    stop_reason = "tool_use" if has_client_tools else "end_turn"

    usage = {
        "input_tokens": input_tokens,
        "output_tokens": total_output_tokens,
    }
    if web_search_count > 0:
        usage["server_tool_use"] = {"web_search_requests": web_search_count}

    logger.debug(
        f"[ServerTools Non-Streaming] Completed: input_tokens={input_tokens}, "
        f"output_tokens={total_output_tokens}, stop_reason={stop_reason}"
    )

    if usage_tracker and api_key_id and kiro_account_id:
        try:
            await usage_tracker.record_request(
                api_key_id=api_key_id, kiro_account_id=kiro_account_id,
                model=model, endpoint="/v1/messages",
                input_tokens=input_tokens, output_tokens=total_output_tokens,
                status_code=200, duration_ms=0,
            )
            await usage_tracker.flush()
        except Exception as e:
            logger.error(f"Failed to record usage: {e}")

    return {
        "id": message_id,
        "type": "message",
        "role": "assistant",
        "content": content_blocks,
        "model": model,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage,
    }


def _build_continuation_payload(
    original_payload: Dict[str, Any],
    assistant_content: str,
    tool_calls: List[Dict[str, Any]],
    tool_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Build a continuation Kiro payload after server-side tool execution.

    Takes the original payload, appends the assistant response + tool results
    to history, and creates a new current message with tool results.
    """
    payload = copy.deepcopy(original_payload)
    conv_state = payload["conversationState"]
    history = conv_state.get("history", [])

    current_msg = conv_state["currentMessage"]["userInputMessage"]
    model_id = current_msg.get("modelId", "")

    # Move current user message to history
    history.append({"userInputMessage": current_msg})

    # Build assistant response with tool uses
    tool_uses = []
    for tc in tool_calls:
        func = tc.get("function", {})
        name = func.get("name", "") or tc.get("name", "")
        arguments = func.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}

        tool_uses.append({
            "name": name,
            "input": arguments,
            "toolUseId": tc.get("id", ""),
        })

    assistant_response = {"content": assistant_content or "(empty)"}
    if tool_uses:
        assistant_response["toolUses"] = tool_uses

    history.append({"assistantResponseMessage": assistant_response})

    # Build new current message with tool results
    kiro_tool_results = []
    for tr in tool_results:
        kiro_tool_results.append({
            "content": [{"text": tr.get("content", "(empty)")}],
            "status": "success",
            "toolUseId": tr.get("tool_use_id", ""),
        })

    new_current = {
        "content": "Continue based on the tool results above.",
        "modelId": model_id,
        "origin": "AI_EDITOR",
        "userInputMessageContext": {},
    }

    # Preserve tools from original payload
    original_context = current_msg.get("userInputMessageContext", {})
    if "tools" in original_context:
        new_current["userInputMessageContext"]["tools"] = original_context["tools"]

    if kiro_tool_results:
        new_current["userInputMessageContext"]["toolResults"] = kiro_tool_results

    if not new_current["userInputMessageContext"]:
        del new_current["userInputMessageContext"]

    conv_state["history"] = history
    conv_state["currentMessage"] = {"userInputMessage": new_current}

    return payload
