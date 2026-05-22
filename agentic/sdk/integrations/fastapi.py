"""FastAPI integration — SSE streaming router with session management.

Usage::

    from fastapi import FastAPI
    from agentic.sdk import Agent
    from agentic.sdk.integrations.fastapi import AgentRouter

    agent = Agent(system_prompt="You are a helpful assistant.")

    app = FastAPI()
    router = AgentRouter(agent, session_ttl=3600)
    app.include_router(router, prefix="/api/agent")

Endpoints created:

    POST /api/agent/chat         — single request/response (JSON)
    POST /api/agent/chat/stream  — Server-Sent Events stream
    DELETE /api/agent/sessions/{session_id}  — clear a session
    GET  /api/agent/sessions     — list active session IDs
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from agentic.sdk.agent import Agent, Session
from agentic.sdk.events import DoneEvent, ErrorEvent, Event


class AgentRouter:
    """FastAPI ``APIRouter`` factory for an :class:`~agentic.sdk.Agent`.

    Manages sessions server-side with optional TTL-based expiry.

    Args:
        agent: The configured :class:`~agentic.sdk.Agent` instance.
        session_ttl: Seconds before an idle session is evicted (default 3600).
            Set to ``0`` to disable expiry.
        prefix: URL prefix for all routes (default ``""``).
        tags: FastAPI router tags (default ``["agent"]``).
    """

    def __init__(
        self,
        agent: Agent,
        session_ttl: int = 3600,
        prefix: str = "",
        tags: list[str] | None = None,
    ) -> None:
        self._agent = agent
        self._ttl = session_ttl
        self._sessions: dict[str, tuple[Session, float]] = {}  # id → (session, last_used)
        self._prefix = prefix
        self._tags = tags or ["agent"]

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def _get_or_create(self, session_id: str | None) -> tuple[str, Session]:
        self._evict_stale()
        if session_id and session_id in self._sessions:
            session, _ = self._sessions[session_id]
            self._sessions[session_id] = (session, time.monotonic())
            return session_id, session
        session = self._agent.session()
        self._sessions[session.id] = (session, time.monotonic())
        return session.id, session

    def _evict_stale(self) -> None:
        if not self._ttl:
            return
        cutoff = time.monotonic() - self._ttl
        stale = [sid for sid, (_, ts) in self._sessions.items() if ts < cutoff]
        for sid in stale:
            del self._sessions[sid]

    # ------------------------------------------------------------------
    # FastAPI router factory
    # ------------------------------------------------------------------

    def __call__(self) -> "Any":  # returns APIRouter
        try:
            from fastapi import APIRouter
            from fastapi.responses import StreamingResponse
            from pydantic import BaseModel
        except ImportError as e:
            raise ImportError(
                "FastAPI is required for AgentRouter. "
                "Install it with: pip install fastapi uvicorn"
            ) from e

        router = APIRouter(prefix=self._prefix, tags=self._tags)

        class ChatRequest(BaseModel):
            message: str
            session_id: str | None = None

        class ChatResponse(BaseModel):
            text: str
            session_id: str
            input_tokens: int = 0
            output_tokens: int = 0
            cost_usd: float = 0.0

        @router.post("/chat", response_model=ChatResponse)
        async def chat(req: ChatRequest) -> ChatResponse:
            """Send a message and receive the complete response."""
            session_id, session = self._get_or_create(req.session_id)
            text = ""
            input_tokens = output_tokens = 0
            cost_usd = 0.0
            async for event in session.stream(req.message):
                if isinstance(event, DoneEvent):
                    text = event.text
                    input_tokens = event.input_tokens
                    output_tokens = event.output_tokens
                    cost_usd = event.cost_usd
            return ChatResponse(
                text=text,
                session_id=session_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
            )

        @router.post("/chat/stream")
        async def chat_stream(req: ChatRequest) -> StreamingResponse:
            """Stream events as Server-Sent Events (text/event-stream).

            Each event is a JSON-encoded line prefixed with ``data: ``.
            The stream ends with a ``data: [DONE]`` sentinel.
            """
            session_id, session = self._get_or_create(req.session_id)

            async def _generate():
                # Send session_id first so the client knows which session was created
                yield f"data: {json.dumps({'type': 'session', 'session_id': session_id})}\n\n"
                async for event in session.stream(req.message):
                    payload = event.to_dict()
                    yield f"data: {json.dumps(payload)}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                _generate(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )

        @router.delete("/sessions/{session_id}")
        async def delete_session(session_id: str) -> dict[str, str]:
            """Clear a session's conversation history."""
            if session_id in self._sessions:
                session, _ = self._sessions.pop(session_id)
                session.reset()
                return {"status": "deleted", "session_id": session_id}
            return {"status": "not_found", "session_id": session_id}

        @router.get("/sessions")
        async def list_sessions() -> dict[str, Any]:
            """List all active session IDs."""
            self._evict_stale()
            return {
                "sessions": list(self._sessions.keys()),
                "count": len(self._sessions),
            }

        return router
