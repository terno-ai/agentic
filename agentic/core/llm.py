"""LLM clients for Anthropic and OpenAI, both emitting the same StreamEvent types."""

from __future__ import annotations

import asyncio
import json
import os
import random
from collections.abc import AsyncIterator
from typing import Any

import anthropic
from anthropic.types import Message

_RETRYABLE_HTTP = {429, 500, 502, 503, 529}
_MAX_RETRIES = 4


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, anthropic.RateLimitError):
        return True
    if isinstance(exc, anthropic.APIStatusError) and exc.status_code in _RETRYABLE_HTTP:
        return True
    if isinstance(exc, anthropic.APIConnectionError):
        return True
    return False


# ---------------------------------------------------------------------------
# Shared StreamEvent types — both clients emit these so the agent loop is
# provider-agnostic.
# ---------------------------------------------------------------------------

class StreamEvent:
    pass


class TextDelta(StreamEvent):
    def __init__(self, text: str):
        self.text = text


class ThinkingDelta(StreamEvent):
    """Incremental thinking content while the thinking block is streaming."""
    def __init__(self, text: str):
        self.text = text


class ThinkingBlockComplete(StreamEvent):
    """A complete thinking block, ready to be stored in conversation history."""
    def __init__(self, thinking: str, signature: str = ""):
        self.thinking = thinking
        self.signature = signature


class ToolUseStart(StreamEvent):
    def __init__(self, tool_use_id: str, tool_name: str):
        self.tool_use_id = tool_use_id
        self.tool_name = tool_name


class ToolInputDelta(StreamEvent):
    def __init__(self, tool_use_id: str, partial_json: str):
        self.tool_use_id = tool_use_id
        self.partial_json = partial_json


class MessageComplete(StreamEvent):
    """Signals the stream is done. The message attribute is provider-specific."""
    def __init__(self, message: Any):
        self.message = message


class UsageInfo(StreamEvent):
    def __init__(self, input_tokens: int, output_tokens: int,
                 cache_read: int = 0, cache_write: int = 0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read = cache_read
        self.cache_write = cache_write

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


# ---------------------------------------------------------------------------
# Anthropic client
# ---------------------------------------------------------------------------

class AnthropicClient:
    def __init__(self, api_key: str | None = None, model: str = "claude-sonnet-4-6"):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model
        self._client = anthropic.Anthropic(api_key=self.api_key)
        self._async_client = anthropic.AsyncAnthropic(api_key=self.api_key)
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    @staticmethod
    def _with_cache_warming(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Add cache_control breakpoint to the last stable message (penultimate turn).

        Anthropic caches everything up to the marked position, so marking the
        second-to-last user message means the whole conversation except the
        current turn is eligible for cache hits on repeated calls.
        """
        if len(messages) < 4:
            return messages

        msgs = [m.copy() for m in messages]
        # Find the second-to-last user message and mark its last content block
        user_indices = [i for i, m in enumerate(msgs) if m.get("role") == "user"]
        if len(user_indices) < 2:
            return msgs

        target_idx = user_indices[-2]
        content = msgs[target_idx].get("content")
        if isinstance(content, list) and content:
            last_block = dict(content[-1])
            last_block["cache_control"] = {"type": "ephemeral"}
            msgs[target_idx] = dict(msgs[target_idx])
            msgs[target_idx]["content"] = list(content[:-1]) + [last_block]
        elif isinstance(content, str):
            msgs[target_idx] = {
                "role": "user",
                "content": [
                    {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
                ],
            }
        return msgs

    async def stream_message(
        self,
        messages: list[dict[str, Any]],
        system: str | list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 8192,
        thinking_budget: int = 0,
    ) -> AsyncIterator[StreamEvent]:
        warmed_messages = self._with_cache_warming(messages)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": warmed_messages,
        }
        if tools:
            kwargs["tools"] = tools
        if thinking_budget > 0:
            # budget_tokens must be < max_tokens; bump max_tokens to fit both
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}
            kwargs["max_tokens"] = max(max_tokens, thinking_budget + 1024)

        last_exc: BaseException | None = None
        for attempt in range(_MAX_RETRIES + 1):
            started = False
            try:
                async with self._async_client.messages.stream(**kwargs) as stream:
                    current_tool_id: str | None = None

                    current_block_type: str = "text"
                    current_thinking_buf: list[str] = []
                    async for event in stream:
                        started = True
                        if hasattr(event, "type"):
                            if event.type == "content_block_start":
                                block = event.content_block
                                current_block_type = block.type
                                if block.type == "thinking":
                                    current_thinking_buf = []
                                elif block.type == "tool_use":
                                    current_tool_id = block.id
                                    yield ToolUseStart(block.id, block.name)
                            elif event.type == "content_block_delta":
                                delta = event.delta
                                if hasattr(delta, "thinking"):
                                    current_thinking_buf.append(delta.thinking)
                                    yield ThinkingDelta(delta.thinking)
                                elif hasattr(delta, "text"):
                                    if current_block_type == "thinking":
                                        current_thinking_buf.append(delta.text)
                                        yield ThinkingDelta(delta.text)
                                    else:
                                        yield TextDelta(delta.text)
                                elif hasattr(delta, "partial_json") and current_tool_id:
                                    yield ToolInputDelta(current_tool_id, delta.partial_json)
                            elif event.type == "content_block_stop":
                                if current_block_type == "thinking" and current_thinking_buf:
                                    yield ThinkingBlockComplete("".join(current_thinking_buf))
                                    current_thinking_buf = []
                            elif event.type == "message_start":
                                self.total_input_tokens += event.message.usage.input_tokens
                            elif event.type == "message_delta":
                                if hasattr(event, "usage") and event.usage:
                                    self.total_output_tokens += event.usage.output_tokens

                    final_msg = await stream.get_final_message()
                    yield UsageInfo(
                        input_tokens=final_msg.usage.input_tokens,
                        output_tokens=final_msg.usage.output_tokens,
                        cache_read=getattr(final_msg.usage, "cache_read_input_tokens", 0) or 0,
                        cache_write=getattr(final_msg.usage, "cache_creation_input_tokens", 0) or 0,
                    )
                    yield MessageComplete(final_msg)
                    return  # success

            except Exception as exc:
                last_exc = exc
                # Never retry if we've already yielded events (partial stream)
                if started or not _is_retryable(exc) or attempt == _MAX_RETRIES:
                    raise

            wait = min(2 ** attempt, 30) + random.random()
            await asyncio.sleep(wait)

        raise last_exc  # type: ignore[misc]

    def create_message(
        self,
        messages: list[dict[str, Any]],
        system: str | list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 8192,
    ) -> Message:
        """Synchronous — used by context summarization."""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
        return self._client.messages.create(**kwargs)

    @property
    def session_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens


# ---------------------------------------------------------------------------
# OpenAI client — same interface, different wire format
# ---------------------------------------------------------------------------

class _TextBlock:
    """Minimal shim so context.py's `for block in response.content: block.text` works."""
    def __init__(self, text: str):
        self.text = text

class _SyncResponse:
    def __init__(self, text: str):
        self.content = [_TextBlock(text)]


def _system_to_text(system: str | list[dict[str, Any]]) -> str:
    """Extract plain text from an Anthropic-format system prompt."""
    if isinstance(system, str):
        return system
    return "\n\n".join(
        block.get("text", "") for block in system
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _anthropic_tools_to_openai(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Anthropic tool schemas → OpenAI function tool schemas."""
    result = []
    for t in tools:
        result.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return result


def _anthropic_messages_to_openai(
    messages: list[dict[str, Any]],
    system: str | list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Convert Anthropic conversation history to OpenAI chat format.

    Anthropic stores tool interaction as:
      assistant: [{type:tool_use, id, name, input}]
      user:      [{type:tool_result, tool_use_id, content}]

    OpenAI expects:
      assistant: {tool_calls:[{id, type:function, function:{name, arguments}}]}
      tool:      {role:tool, tool_call_id, content}
    """
    result: list[dict[str, Any]] = []

    # System message first
    system_text = _system_to_text(system)
    if system_text:
        result.append({"role": "system", "content": system_text})

    for msg in messages:
        role = msg["role"]
        content = msg["content"]

        if isinstance(content, str):
            result.append({"role": role, "content": content})
            continue

        if role == "user":
            tool_results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
            text_blocks  = [b for b in content if isinstance(b, dict) and b.get("type") == "text"]
            plain_text   = [b for b in content if isinstance(b, str)]

            for tr in tool_results:
                result.append({
                    "role": "tool",
                    "tool_call_id": tr["tool_use_id"],
                    "content": tr.get("content", ""),
                })

            text = "".join(b.get("text", "") for b in text_blocks) + "".join(plain_text)
            if text:
                result.append({"role": "user", "content": text})

        elif role == "assistant":
            text_parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            tool_uses  = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
            text = "".join(text_parts)

            if tool_uses:
                tool_calls = [
                    {
                        "id": tu["id"],
                        "type": "function",
                        "function": {
                            "name": tu["name"],
                            "arguments": json.dumps(tu.get("input", {})),
                        },
                    }
                    for tu in tool_uses
                ]
                openai_msg: dict[str, Any] = {"role": "assistant", "tool_calls": tool_calls}
                if text:
                    openai_msg["content"] = text
                result.append(openai_msg)
            else:
                result.append({"role": "assistant", "content": text})

    return result


class OpenAIClient:
    def __init__(self, api_key: str | None = None, model: str = "gpt-4o"):
        import openai as _openai
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.model = model
        self._client = _openai.OpenAI(api_key=self.api_key)
        self._async_client = _openai.AsyncOpenAI(api_key=self.api_key)
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    def _tokens_kwarg(self, max_tokens: int) -> dict[str, Any]:
        # OpenAI is deprecating max_tokens across all models in favour of
        # max_completion_tokens. Newer models (gpt-5, gpt-5.5, o-series)
        # reject max_tokens outright. Using max_completion_tokens universally
        # is safe — older models accept both.
        return {"max_completion_tokens": max_tokens}

    async def stream_message(
        self,
        messages: list[dict[str, Any]],
        system: str | list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 8192,
        thinking_budget: int = 0,  # accepted but ignored — OpenAI has no equivalent
    ) -> AsyncIterator[StreamEvent]:
        openai_msgs = _anthropic_messages_to_openai(messages, system)
        openai_tools = _anthropic_tools_to_openai(tools) if tools else None

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": openai_msgs,
            **self._tokens_kwarg(max_tokens),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if openai_tools:
            kwargs["tools"] = openai_tools

        last_exc: BaseException | None = None
        for attempt in range(_MAX_RETRIES + 1):
            started = False
            tool_state: dict[int, dict[str, str]] = {}   # index → {id, name}
            arg_buf: dict[int, list[str]] = {}            # args buffered before id arrives
            try:
                stream = await self._async_client.chat.completions.create(**kwargs)
                async for chunk in stream:
                    started = True
                    # Final usage chunk has no choices
                    if not chunk.choices:
                        if chunk.usage:
                            inp = chunk.usage.prompt_tokens
                            out = chunk.usage.completion_tokens
                            self.total_input_tokens += inp
                            self.total_output_tokens += out
                            yield UsageInfo(input_tokens=inp, output_tokens=out)
                        continue

                    choice = chunk.choices[0]
                    delta = choice.delta

                    if delta.content:
                        yield TextDelta(delta.content)

                    if delta.tool_calls:
                        for tc_delta in delta.tool_calls:
                            idx = tc_delta.index
                            if tc_delta.id:
                                tool_state[idx] = {
                                    "id": tc_delta.id,
                                    "name": (tc_delta.function.name or "") if tc_delta.function else "",
                                }
                                yield ToolUseStart(tc_delta.id, tool_state[idx]["name"])
                                # Flush any arguments that arrived before the id
                                for buffered in arg_buf.pop(idx, []):
                                    yield ToolInputDelta(tc_delta.id, buffered)
                            # Collect argument fragments (use `is not None` to keep empty strings)
                            if tc_delta.function and tc_delta.function.arguments is not None:
                                args = tc_delta.function.arguments
                                if idx in tool_state:
                                    yield ToolInputDelta(tool_state[idx]["id"], args)
                                else:
                                    # id not yet seen — buffer until it arrives
                                    arg_buf.setdefault(idx, []).append(args)

                yield MessageComplete(None)
                return  # success

            except Exception as exc:
                last_exc = exc
                if started or not _is_retryable(exc) or attempt == _MAX_RETRIES:
                    raise

            wait = min(2 ** attempt, 30) + random.random()
            await asyncio.sleep(wait)

        raise last_exc  # type: ignore[misc]

    def create_message(
        self,
        messages: list[dict[str, Any]],
        system: str | list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 8192,
    ) -> _SyncResponse:
        """Synchronous — used by context summarization. Returns object compatible with Anthropic response."""
        openai_msgs = _anthropic_messages_to_openai(messages, system)
        openai_tools = _anthropic_tools_to_openai(tools) if tools else None

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": openai_msgs,
            **self._tokens_kwarg(max_tokens),
        }
        if openai_tools:
            kwargs["tools"] = openai_tools

        response = self._client.chat.completions.create(**kwargs)
        text = response.choices[0].message.content or ""
        return _SyncResponse(text)

    @property
    def session_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_llm_client(
    provider: str,
    model: str,
    api_key: str = "",
    openai_api_key: str = "",
) -> AnthropicClient | OpenAIClient:
    """Return the right client based on provider. Auto-detects from model name if provider is blank."""
    from agentic.core.config import detect_provider
    resolved = provider or detect_provider(model)
    if resolved == "openai":
        return OpenAIClient(api_key=openai_api_key or None, model=model)
    return AnthropicClient(api_key=api_key or None, model=model)
