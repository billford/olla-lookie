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
    return FileResponse(DASHBOARD_HTML, media_type="text/html")


@app.get("/health")
async def health():
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
    return {"lines": _read_last_lines(LOG_FILE, lines)}


def _clean(line: str) -> str:
    return _ANSI_RE.sub("", line)


def _keep(line: str) -> bool:
    """Olla emits each event in both text and JSON format. Keep only JSON lines
    (start with '{') to avoid duplicates. Plain text lines (no 'time=' prefix)
    are also kept in case the log format changes."""
    s = line.lstrip()
    if s.startswith("time="):
        return False
    return bool(s)


def _read_last_lines(path: str, n: int) -> list[str]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return []
            buf = b""
            pos = size
            while pos > 0:
                read = min(8192, pos)
                pos -= read
                f.seek(pos)
                buf = f.read(read) + buf
                lines = [_clean(l) for l in buf.decode("utf-8", errors="replace").splitlines() if _keep(l)]
                if len(lines) > n:
                    return lines[-n:]
            return [_clean(l) for l in buf.decode("utf-8", errors="replace").splitlines() if _keep(l)][-n:]
    except OSError:
        return []


async def _tail_generator(path: str) -> AsyncGenerator[str, None]:
    PING_EVERY = 15.0
    POLL = 0.1
    last_ping = asyncio.get_event_loop().time()
    fh = None
    try:
        while True:
            if fh is None:
                if not os.path.exists(path):
                    yield f"data: [log file not found: {path}]\n\n"
                    await asyncio.sleep(5)
                    continue
                fh = open(path, "r", encoding="utf-8", errors="replace")
                fh.seek(0, 2)

            line = fh.readline()
            if line:
                if _keep(line):
                    yield f"data: {_clean(line.rstrip())}\n\n"
                last_ping = asyncio.get_event_loop().time()
            else:
                now = asyncio.get_event_loop().time()
                if now - last_ping >= PING_EVERY:
                    yield ": ping\n\n"
                    last_ping = now
                await asyncio.sleep(POLL)
    except OSError as exc:
        yield f"data: [log error: {exc}]\n\n"
    finally:
        if fh:
            fh.close()


@app.get("/logs/stream")
async def logs_stream():
    return StreamingResponse(
        _tail_generator(LOG_FILE),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
