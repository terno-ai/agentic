"""Tests for LLM client utilities — format converters and factory."""

import json
import pytest

from agentic.core.config import detect_provider
from agentic.core.llm import (
    _anthropic_messages_to_openai,
    _anthropic_tools_to_openai,
    _system_to_text,
    create_llm_client,
    AnthropicClient,
    OpenAIClient,
)


# ---------------------------------------------------------------------------
# detect_provider
# ---------------------------------------------------------------------------

def test_detect_openai_gpt():
    assert detect_provider("gpt-4o") == "openai"

def test_detect_openai_gpt4():
    assert detect_provider("gpt-4-turbo") == "openai"

def test_detect_openai_o1():
    assert detect_provider("o1") == "openai"

def test_detect_openai_o3():
    assert detect_provider("o3-mini") == "openai"

def test_detect_anthropic_claude():
    assert detect_provider("claude-sonnet-4-6") == "anthropic"

def test_detect_anthropic_default():
    assert detect_provider("unknown-model") == "anthropic"


# ---------------------------------------------------------------------------
# _system_to_text
# ---------------------------------------------------------------------------

def test_system_plain_string():
    assert _system_to_text("hello") == "hello"

def test_system_list_of_blocks():
    blocks = [
        {"type": "text", "text": "Part one.", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "Part two."},
    ]
    result = _system_to_text(blocks)
    assert "Part one." in result
    assert "Part two." in result


# ---------------------------------------------------------------------------
# _anthropic_tools_to_openai
# ---------------------------------------------------------------------------

def test_tools_conversion_basic():
    anthropic_tools = [
        {
            "name": "Bash",
            "description": "Run a shell command",
            "input_schema": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        }
    ]
    result = _anthropic_tools_to_openai(anthropic_tools)
    assert len(result) == 1
    assert result[0]["type"] == "function"
    assert result[0]["function"]["name"] == "Bash"
    assert result[0]["function"]["description"] == "Run a shell command"
    assert "command" in result[0]["function"]["parameters"]["properties"]

def test_tools_conversion_empty():
    assert _anthropic_tools_to_openai([]) == []


# ---------------------------------------------------------------------------
# _anthropic_messages_to_openai
# ---------------------------------------------------------------------------

def test_plain_user_message():
    msgs = [{"role": "user", "content": "hello"}]
    result = _anthropic_messages_to_openai(msgs, system="sys")
    assert result[0] == {"role": "system", "content": "sys"}
    assert result[1] == {"role": "user", "content": "hello"}

def test_plain_assistant_message():
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello back"},
    ]
    result = _anthropic_messages_to_openai(msgs, system="")
    user_msg = next(m for m in result if m["role"] == "user")
    asst_msg = next(m for m in result if m["role"] == "assistant")
    assert user_msg["content"] == "hi"
    assert asst_msg["content"] == "hello back"

def test_tool_use_in_assistant():
    msgs = [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "I'll run this."},
                {"type": "tool_use", "id": "abc123", "name": "Bash", "input": {"command": "ls"}},
            ],
        }
    ]
    result = _anthropic_messages_to_openai(msgs, system="")
    asst = next(m for m in result if m["role"] == "assistant")
    assert "tool_calls" in asst
    assert asst["tool_calls"][0]["id"] == "abc123"
    assert asst["tool_calls"][0]["function"]["name"] == "Bash"
    parsed_args = json.loads(asst["tool_calls"][0]["function"]["arguments"])
    assert parsed_args == {"command": "ls"}
    assert asst.get("content") == "I'll run this."

def test_tool_result_becomes_tool_role():
    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "abc123", "content": "file1.txt\nfile2.txt"},
            ],
        }
    ]
    result = _anthropic_messages_to_openai(msgs, system="")
    tool_msg = next(m for m in result if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == "abc123"
    assert tool_msg["content"] == "file1.txt\nfile2.txt"

def test_full_tool_round_trip():
    """user → assistant(tool_use) → user(tool_result) → assistant(text)"""
    msgs = [
        {"role": "user", "content": "list files"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "file.txt"},
            ],
        },
        {"role": "assistant", "content": "There is one file: file.txt"},
    ]
    result = _anthropic_messages_to_openai(msgs, system="sys")
    roles = [m["role"] for m in result]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]

def test_system_injected_once():
    msgs = [{"role": "user", "content": "hi"}]
    result = _anthropic_messages_to_openai(msgs, system="my system")
    system_msgs = [m for m in result if m["role"] == "system"]
    assert len(system_msgs) == 1
    assert system_msgs[0]["content"] == "my system"

def test_empty_system_not_injected():
    msgs = [{"role": "user", "content": "hi"}]
    result = _anthropic_messages_to_openai(msgs, system="")
    system_msgs = [m for m in result if m["role"] == "system"]
    assert len(system_msgs) == 0


# ---------------------------------------------------------------------------
# create_llm_client factory
# ---------------------------------------------------------------------------

def test_factory_returns_anthropic_for_claude():
    client = create_llm_client(provider="", model="claude-sonnet-4-6", api_key="sk-ant-fake")
    assert isinstance(client, AnthropicClient)
    assert client.model == "claude-sonnet-4-6"

def test_factory_returns_openai_for_gpt():
    client = create_llm_client(provider="", model="gpt-4o", openai_api_key="sk-fake")
    assert isinstance(client, OpenAIClient)
    assert client.model == "gpt-4o"

def test_factory_explicit_provider_overrides_model_name():
    # Even though model looks like Claude, explicit provider=openai wins
    client = create_llm_client(
        provider="openai",
        model="claude-sonnet-4-6",  # unusual but explicit wins
        openai_api_key="sk-fake",
    )
    assert isinstance(client, OpenAIClient)

def test_factory_explicit_anthropic():
    client = create_llm_client(provider="anthropic", model="gpt-4o", api_key="sk-ant-fake")
    assert isinstance(client, AnthropicClient)
