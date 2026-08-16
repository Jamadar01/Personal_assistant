"""HTTP backend for the assistant.

    uvicorn server:app --reload --port 8000

The agent lives in main.py. This file only moves messages between it and an HTTP client.
Replies stream back over SSE as the model writes them.
"""

import asyncio
import base64
import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from pydantic import BaseModel

from main import build_agent

# DEBUG_EVENTS=1 logs what each turn does and when. Off by default - it prints tool
# arguments, which for this agent means fragments of the user's mail.
DEBUG_EVENTS = os.environ.get("DEBUG_EVENTS") == "1"

# Set APP_PASSWORD on any host that is reachable from the internet. Unset means the app
# is open, which is only ever right on localhost.
APP_PASSWORD = os.environ.get("APP_PASSWORD")

# conversation id -> the real message list, including tool calls and their results.
# In memory on purpose: this is a single-user app and history dies with the process.
CONVERSATIONS: dict[str, list] = {}

AGENT = None
SYSTEM_PROMPT = None

# The MCP sessions are shared between requests, so two turns running at once would
# interleave their stdio traffic. One turn at a time.
TURN_LOCK = asyncio.Lock()

# Both MCP servers read their Google credentials from fixed paths on disk and have no
# way to take them from the environment. A deployed container has no such files and
# cannot run the browser OAuth flow to create them, so the already-authorised contents
# are supplied as secrets and written out here before the servers start.
CREDENTIAL_FILES = {
    "GOOGLE_OAUTH_KEYS": Path.home() / ".gmail-mcp" / "gcp-oauth.keys.json",
    "GMAIL_CREDENTIALS": Path.home() / ".gmail-mcp" / "credentials.json",
    "CALENDAR_TOKENS": Path.home() / ".config" / "google-calendar-mcp" / "tokens.json",
}


def write_credentials() -> None:
    """Turn the credential secrets into the files the MCP servers expect.

    Locally none of these variables are set and the real files are already in place, so
    this does nothing.
    """
    for variable, path in CREDENTIAL_FILES.items():
        blob = os.environ.get(variable)
        if not blob:
            continue

        # A secret that got truncated on the way in would otherwise surface much later
        # as an MCP server that starts and then hangs. Fail here, where it is obvious.
        try:
            json.loads(blob)
        except json.JSONDecodeError as e:
            raise RuntimeError("%s is not valid JSON: %s" % (variable, e)) from e

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(blob, encoding="utf-8")
        path.chmod(0o600)
        # The path only - never the contents, which are live Google tokens.
        print("wrote credentials to %s" % path)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Start the MCP servers once, on the loop that will serve every request.

    The sessions inside the agent are bound to the loop that created them, so this has
    to happen here rather than at import time.
    """
    global AGENT, SYSTEM_PROMPT
    write_credentials()
    AGENT, SYSTEM_PROMPT = await build_agent()
    print("MCP servers up. Assistant ready.")
    yield


app = FastAPI(title="Sara", lifespan=lifespan)


@app.middleware("http")
async def require_password(request: Request, call_next):
    """Guard everything behind HTTP Basic auth when APP_PASSWORD is set.

    Basic auth is used because the browser handles it: a 401 on the page itself brings up
    the native login box, and every later fetch carries the credentials automatically.
    That keeps the whole login story out of the front end. The username is ignored - only
    the password is checked.

    It also guards the static files deliberately, not just /api. Guarding the API alone
    would leave the page loading for anyone, and the prompt would never appear.
    """
    # Open the health check up, so a host can poll readiness without the password. It
    # reports a boolean and nothing else.
    if APP_PASSWORD and request.url.path != "/api/health":
        supplied = ""
        header = request.headers.get("authorization", "")
        if header.startswith("Basic "):
            try:
                _, _, supplied = base64.b64decode(header[6:]).decode().partition(":")
            except (ValueError, UnicodeDecodeError):
                supplied = ""

        # compare_digest rather than == so the comparison does not leak the password one
        # character at a time through how long it takes to fail.
        if not secrets.compare_digest(supplied, APP_PASSWORD):
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Sara"'},
            )

    return await call_next(request)


class ChatRequest(BaseModel):
    message: str
    conversation_id: str = "default"


def text_of(content) -> str:
    """Flatten message content to a string.

    Some models return content as a list of blocks rather than a plain string.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") != "tool_use"
        )
    return ""


def log(started: float, message: str) -> None:
    """Print a line stamped with how far into the turn we are.

    The elapsed time is the point: if the first token lands seconds before the last one,
    the stream is genuinely streaming rather than arriving in one lump at the end.
    """
    if DEBUG_EVENTS:
        print("[%6.2fs] %s" % (time.perf_counter() - started, message), flush=True)


def sse(event: dict) -> str:
    """Format one event the way the EventSource/fetch reader on the client expects."""
    return "data: %s\n\n" % json.dumps(event)


@app.get("/api/health")
async def health():
    return {"ready": AGENT is not None}


@app.post("/api/reset")
async def reset(body: ChatRequest):
    CONVERSATIONS.pop(body.conversation_id, None)
    return {"ok": True}


async def run_turn(message: str, conversation_id: str):
    """Run one turn, yielding an event dict each time something happens.

    Two kinds of event come out:
      {"type": "token", "text": ...}   a piece of the reply, as the model writes it
      {"type": "tool",  "name": ...}   the agent stopped to call a tool
    """
    history = CONVERSATIONS.get(conversation_id) or [
        {"role": "system", "content": SYSTEM_PROMPT}
    ]
    base = history + [{"role": "user", "content": message}]
    new_messages = []  # everything the graph appends this turn

    started = time.perf_counter()
    tokens = 0
    log(started, "turn start: %r (history: %d messages)" % (message[:60], len(history)))

    # "messages" gives tokens as the model writes them; "updates" gives whole messages as
    # each node finishes. Both are needed - tokens alone would never say a tool was used.
    async for mode, chunk in AGENT.astream(
        {"messages": base}, stream_mode=["updates", "messages"]
    ):
        if mode == "messages":
            piece, _meta = chunk
            if not isinstance(piece, AIMessageChunk):
                continue
            # Tool-call arguments arrive here too, as chunks whose content is empty -
            # text_of drops those, so only real prose is sent on.
            text = text_of(piece.content)
            if text:
                tokens += 1
                # Only the first one, then a count at the end. Logging every token would
                # bury the tool calls, which are the interesting part.
                if tokens == 1:
                    log(started, "first token")
                yield {"type": "token", "text": text}

        else:
            for update in chunk.values():
                for m in update.get("messages", []):
                    new_messages.append(m)
                    if isinstance(m, AIMessage) and m.tool_calls:
                        for tc in m.tool_calls:
                            # The arguments are what the chips in the UI cannot show, and
                            # they are where a wrong answer usually starts.
                            log(started, "tool -> %s(%s)" % (tc["name"], json.dumps(tc.get("args") or {}, default=str)))
                            yield {"type": "tool", "name": tc["name"]}
                    elif isinstance(m, ToolMessage):
                        result = text_of(m.content) or str(m.content)
                        log(started, "     <- %s: %s" % (m.name, result[:120].replace("\n", " ")))

    log(started, "turn done: %d tokens, %d new messages" % (tokens, len(new_messages)))

    # Only saved if the loop finished. A failed turn is dropped, so the history never
    # ends on a user message the agent did not answer.
    CONVERSATIONS[conversation_id] = base + new_messages


@app.post("/api/chat")
async def chat(body: ChatRequest):
    """One turn, streamed back as SSE - one JSON object per `data:` line."""
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Empty message.")
    if AGENT is None:
        raise HTTPException(status_code=503, detail="Still starting up, try again.")

    async def stream():
        # Held for the whole turn: the MCP sessions cannot serve two turns at once.
        async with TURN_LOCK:
            try:
                async for event in run_turn(message, body.conversation_id):
                    yield sse(event)
            except Exception as e:  # noqa: BLE001 - report it, do not kill the server
                # Headers are long gone by now, so a 500 is not an option - the failure
                # has to travel as an event like everything else.
                yield sse({"type": "error", "message": "%s: %s" % (type(e).__name__, e)})
            yield sse({"type": "done"})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        # Without these a proxy will happily buffer the whole stream and hand it over in
        # one lump at the end, which defeats the point of streaming.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Mounted last, so it cannot shadow the /api routes declared above. Only present once
# `npm run build` has been run - in development Vite serves the front end instead and
# proxies /api back here, so this stays absent and nothing breaks.
DIST = Path(__file__).parent / "web" / "dist"
if DIST.is_dir():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="web")
else:
    print("web/dist not found - serving the API only. Run `npm run build` in web/.")


if __name__ == "__main__":
    import uvicorn

    # Hosts hand you the port to listen on rather than letting you pick, and expect the
    # process to bind every interface, not just loopback.
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
