"""FastAPI backend for the Olla cluster dashboard.

Serves the static dashboard HTML and provides two endpoints:
  - /internal/* : proxy to the Olla internal API (avoids browser CORS issues)
  - /logs/recent : last N lines from the Olla log file
  - /logs/stream : SSE tail of the Olla log file
"""

import asyncio
import os
import re
from typing import AsyncGenerator

import httpx
from fastapi import FastAPI, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

app = FastAPI(title="Olla Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

LOG_FILE       = "/opt/olla/logs/olla.log"
OLLA_BASE      = "http://192.168.1.182:40114"
DASHBOARD_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "olla_dashboard.html")


@app.get("/", response_class=FileResponse, include_in_schema=False)
async def serve_dashboard():
    """Serve the dashboard HTML file."""
    return FileResponse(DASHBOARD_HTML, media_type="text/html")


@app.get("/health")
async def health():
    """Return service health status."""
    return {"status": "ok"}


@app.get("/internal/{path:path}")
async def proxy_olla(path: str, response: Response):
    """Proxy Olla internal API to avoid browser CORS restrictions."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{OLLA_BASE}/internal/{path}")
    response.status_code = r.status_code
    return r.json()


@app.get("/logs/recent")
async def logs_recent(lines: int = Query(default=100, ge=1, le=500)):
    """Return the last N lines from the Olla log file."""
    return {"lines": _read_last_lines(LOG_FILE, lines)}


def _clean(line: str) -> str:
    """Strip ANSI escape codes from a log line."""
    return _ANSI_RE.sub("", line)


def _keep(line: str) -> bool:
    """Return True for lines that should be shown in the log viewer.

    Olla emits each event in both structured-text and JSON format. Dropping
    lines that start with 'time=' removes the duplicates while keeping JSON
    lines and any plain-text messages.
    """
    stripped = line.lstrip()
    if stripped.startswith("time="):
        return False
    return bool(stripped)


def _read_last_lines(path: str, n: int) -> list[str]:
    """Read the last n lines from path efficiently without loading the whole file."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, "rb") as file_handle:
            file_handle.seek(0, 2)
            size = file_handle.tell()
            if size == 0:
                return []
            buf = b""
            pos = size
            while pos > 0:
                read = min(8192, pos)
                pos -= read
                file_handle.seek(pos)
                buf = file_handle.read(read) + buf
                decoded = buf.decode("utf-8", errors="replace").splitlines()
                lines = [_clean(ln) for ln in decoded if _keep(ln)]
                if len(lines) > n:
                    return lines[-n:]
            decoded = buf.decode("utf-8", errors="replace").splitlines()
            return [_clean(ln) for ln in decoded if _keep(ln)][-n:]
    except OSError:
        return []


async def _tail_generator(path: str) -> AsyncGenerator[str, None]:
    """Yield SSE-formatted lines by tailing path, sending keep-alive pings when idle."""
    ping_every = 15.0
    poll = 0.1
    last_ping = asyncio.get_event_loop().time()
    file_handle = None
    try:
        while True:
            if file_handle is None:
                if not os.path.exists(path):
                    yield f"data: [log file not found: {path}]\n\n"
                    await asyncio.sleep(5)
                    continue
                file_handle = open(path, "r", encoding="utf-8", errors="replace")  # noqa: WPS515
                file_handle.seek(0, 2)

            line = file_handle.readline()
            if line:
                if _keep(line):
                    yield f"data: {_clean(line.rstrip())}\n\n"
                last_ping = asyncio.get_event_loop().time()
            else:
                now = asyncio.get_event_loop().time()
                if now - last_ping >= ping_every:
                    yield ": ping\n\n"
                    last_ping = now
                await asyncio.sleep(poll)
    except OSError as exc:
        yield f"data: [log error: {exc}]\n\n"
    finally:
        if file_handle:
            file_handle.close()


@app.get("/logs/stream")
async def logs_stream():
    """Stream new log lines as Server-Sent Events."""
    return StreamingResponse(
        _tail_generator(LOG_FILE),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
