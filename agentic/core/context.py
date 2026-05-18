"""Context management: token tracking and automatic summarization."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

# $/million tokens — input, output, cache_read, cache_write
# (approximate public list prices as of mid-2025; update as needed)
_MODEL_PRICING: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-4-7":            (15.00, 75.00, 1.50,  18.75),
    "claude-sonnet-4-6":          ( 3.00, 15.00, 0.30,   3.75),
    "claude-haiku-4-5-20251001":  ( 0.80,  4.00, 0.08,   1.00),
    "claude-3-7-sonnet":          ( 3.00, 15.00, 0.30,   3.75),
    "gpt-4o":                     ( 2.50, 10.00, 1.25,   0.00),
    "gpt-4o-mini":                ( 0.15,  0.60, 0.075,  0.00),
    "o4-mini":                    ( 1.10,  4.40, 0.275,  0.00),
    "o3":                         ( 10.0, 40.00, 2.50,   0.00),
}

def _cost_usd(model: str, inp: int, out: int, cache_read: int, cache_write: int) -> float:
    key = next((k for k in _MODEL_PRICING if model.startswith(k)), None)
    if key is None:
        return 0.0
    p_in, p_out, p_cr, p_cw = _MODEL_PRICING[key]
    return (inp * p_in + out * p_out + cache_read * p_cr + cache_write * p_cw) / 1_000_000

if TYPE_CHECKING:
    from agentic.core.conversation import ConversationHistory
    from agentic.core.llm import AnthropicClient

SUMMARIZE_PROMPT = """Produce a concise but complete summary of the conversation so far.

You MUST include ALL of the following — missing any one will cause the agent to lose context:

1. **Goal** — What the user is trying to build or accomplish.

2. **Project facts** (CRITICAL — never omit these):
   - Platform / target environment (browser, mobile, server, CLI, desktop, …)
   - Language(s) and framework(s) in use (JavaScript, Python, React, Django, …)
   - Entry point and key files (index.html, main.py, App.tsx, …)
   - Any constraints the user stated (no backend, offline-only, specific library, …)

3. **Work done so far** — Files created or modified (with their paths and purpose).

4. **Commands run** and what their output showed (briefly).

5. **Current state** — What was last being worked on, what is done, what is left.

6. **Decisions and rationale** — Any choices made and why.

7. **Errors or blockers** encountered and how they were resolved (or not).

Write in past tense, structured paragraphs. Be specific — vague summaries cause the agent to
make wrong assumptions (e.g., looking for main.py in a browser-only project).
Limit to 2500 tokens."""

# Number of most-recent tool-result messages to keep verbatim after summarization.
# These are the raw file reads / command outputs the agent most likely needs right now.
_KEEP_TOOL_TURNS = 3


def _extract_tool_turns(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the last N assistant+tool_result pairs from the message list."""
    pairs: list[tuple[int, int]] = []  # (assistant_idx, tool_result_idx)
    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg["role"] == "assistant":
            content = msg.get("content", [])
            if isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") == "tool_use" for b in content
            ):
                # Next message(s) should be the tool results
                if i + 1 < len(messages) and messages[i + 1]["role"] == "user":
                    user_content = messages[i + 1].get("content", [])
                    if isinstance(user_content, list) and any(
                        isinstance(b, dict) and b.get("type") == "tool_result" for b in user_content
                    ):
                        pairs.append((i, i + 1))
                        i += 2
                        continue
        i += 1

    # Return the last _KEEP_TOOL_TURNS pairs, flattened
    kept = pairs[-_KEEP_TOOL_TURNS:]
    result = []
    for a_idx, t_idx in kept:
        result.append(messages[a_idx])
        result.append(messages[t_idx])
    return result


class ContextManager:
    """Tracks token usage and triggers summarization when needed."""

    def __init__(
        self,
        client: "AnthropicClient",
        conversation: "ConversationHistory",
        summarize_threshold: int = 80_000,
        keep_recent: int = 10,
    ):
        self._client = client
        self._conversation = conversation
        self.summarize_threshold = summarize_threshold
        self.keep_recent = keep_recent
        self._last_usage: dict[str, int] = {}
        self.summarization_count = 0
        self._session_cost: float = 0.0

    def update_usage(self, input_tokens: int, output_tokens: int,
                     cache_read: int = 0, cache_write: int = 0) -> None:
        self._last_usage = {
            "input": input_tokens,
            "output": output_tokens,
            "cache_read": cache_read,
            "cache_write": cache_write,
        }
        model = getattr(self._client, "model", "")
        self._session_cost += _cost_usd(model, input_tokens, output_tokens, cache_read, cache_write)

    @property
    def session_cost(self) -> float:
        return self._session_cost

    @property
    def last_input_tokens(self) -> int:
        return self._last_usage.get("input", 0)

    def should_summarize(self) -> bool:
        return self.last_input_tokens > self.summarize_threshold

    def summarize(self, system_prompt: str | list[dict[str, Any]]) -> str | None:
        """Summarize old messages, keeping recent text turns + recent tool results verbatim."""
        msgs = self._conversation.messages
        if len(msgs) <= self.keep_recent + 2:
            return None

        to_summarize = msgs[:-self.keep_recent]
        if not to_summarize:
            return None

        summary_request = [
            *to_summarize,
            {"role": "user", "content": SUMMARIZE_PROMPT},
        ]

        try:
            response = self._client.create_message(
                messages=summary_request,
                system=system_prompt,
                max_tokens=2048,
            )
            summary = "".join(
                block.text for block in response.content if hasattr(block, "text")
            )

            # Build the replacement: summary prefix + recent tool turns + recent text turns
            recent_msgs = msgs[-self.keep_recent:]
            tool_turns = _extract_tool_turns(to_summarize)

            # De-duplicate: if a tool turn is already in recent_msgs, don't add it twice
            recent_ids = {id(m) for m in recent_msgs}
            extra_tool_msgs = [m for m in tool_turns if id(m) not in recent_ids]

            summary_prefix = [
                {
                    "role": "user",
                    "content": (
                        f"<conversation_summary>\n{summary}\n</conversation_summary>\n\n"
                        "The above is a summary of the earlier conversation. "
                        "Continue from where we left off."
                    ),
                },
                {
                    "role": "assistant",
                    "content": "Understood. I'll continue with full context from the summary.",
                },
            ]

            self._conversation._messages = summary_prefix + extra_tool_msgs + recent_msgs
            replaced = len(msgs) - len(self._conversation._messages)
            self.summarization_count += 1
            return summary if replaced > 0 else None
        except Exception:
            return None

    def status_line(self) -> str:
        inp = self.last_input_tokens
        out = self._last_usage.get("output", 0)
        cache = self._last_usage.get("cache_read", 0)
        pct = int(inp / self.summarize_threshold * 100) if self.summarize_threshold else 0
        parts = [f"~{inp:,}↑ {out:,}↓ tokens"]
        if cache:
            parts.append(f"{cache:,} cached")
        parts.append(f"ctx {pct}%")
        return " · ".join(parts)
