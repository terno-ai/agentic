"""Context management: token tracking and automatic summarization."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

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

    def update_usage(self, input_tokens: int, output_tokens: int,
                     cache_read: int = 0, cache_write: int = 0) -> None:
        self._last_usage = {
            "input": input_tokens,
            "output": output_tokens,
            "cache_read": cache_read,
            "cache_write": cache_write,
        }

    @property
    def last_input_tokens(self) -> int:
        return self._last_usage.get("input", 0)

    def should_summarize(self) -> bool:
        return self.last_input_tokens > self.summarize_threshold

    def summarize(self, system_prompt: str | list[dict[str, Any]]) -> str | None:
        """Summarize the conversation and replace old messages. Returns summary text."""
        if len(self._conversation.messages) <= self.keep_recent + 2:
            return None

        messages_to_summarize = self._conversation.messages[:-self.keep_recent]
        if not messages_to_summarize:
            return None

        # Build a minimal conversation for summarization
        summary_request = [
            *messages_to_summarize,
            {
                "role": "user",
                "content": SUMMARIZE_PROMPT,
            },
        ]

        try:
            response = self._client.create_message(
                messages=summary_request,
                system=system_prompt,
                max_tokens=2048,
            )
            summary = ""
            for block in response.content:
                if hasattr(block, "text"):
                    summary += block.text

            replaced = self._conversation.replace_with_summary(summary, self.keep_recent)
            self.summarization_count += 1
            return summary if replaced > 0 else None
        except Exception as e:
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
