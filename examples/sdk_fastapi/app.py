"""FastAPI web app using the Agentic SDK with SSE streaming.

Install extras:
    pip install fastapi uvicorn

Run:
    uvicorn examples.sdk_fastapi.app:app --reload
    # then open http://localhost:8000

The /api/agent router provides:
    POST /api/agent/chat          — blocking JSON response
    POST /api/agent/chat/stream   — Server-Sent Events stream
    GET  /api/agent/sessions      — list active sessions
    DELETE /api/agent/sessions/{id} — clear a session
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agentic import Agent
from agentic.sdk.integrations.fastapi import AgentRouter

# ---------------------------------------------------------------------------
# Configure the agent
# ---------------------------------------------------------------------------

agent = Agent(
    model="claude-sonnet-4-6",
    system_prompt=(
        "You are a helpful assistant embedded in a web application. "
        "Be concise and friendly. Use markdown formatting in your responses."
    ),
    tools=["WebSearch", "WebFetch"],  # only web tools — no filesystem access
    memory=True,
)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="Agentic SDK Demo", version="1.0.0")

# Mount the SDK router under /api/agent
agent_router_factory = AgentRouter(agent, session_ttl=1800)
app.include_router(agent_router_factory(), prefix="/api/agent")

# Serve the static chat UI
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(str(static_dir / "index.html"))


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
