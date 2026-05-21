# olla-lookie

A lightweight read-only dashboard for monitoring an [Olla](https://github.com/thushan/olla) LLM proxy cluster.

Single-file HTML/JS/CSS frontend + minimal FastAPI backend for log streaming. No build step, no framework, no external dependencies.

![Dark theme dashboard showing node cards, models table, and live log viewer]

## Features

- **Cluster health** — overall status, node count, routable count
- **Node cards** — per-node status, response time, model count, request count, relative load bar
- **Models table** — sortable by name, family, size, params, quant; node availability indicator
- **Live log viewer** — SSE-streamed from the Olla log file, color-coded by level, auto-scroll, 500-line buffer
- **Auto-refresh** — every 30 seconds with countdown, manual refresh button
- Dark theme, monospace values, responsive layout — no CDN dependencies

## Architecture

```
Browser → FastAPI :8080 → Olla proxy :40114 → Ollama nodes
                 ↓
           /logs/stream (SSE)
           /logs/recent
```

FastAPI proxies all Olla internal API calls to avoid CORS restrictions. The browser only ever talks to port 8080.

## Setup

```bash
cd /opt/olla/dashboard
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

**Edit the config block** at the top of `main.py` and in `olla_dashboard.html`:

```python
# main.py
LOG_FILE   = "/opt/olla/logs/olla.log"
OLLA_BASE  = "http://192.168.1.182:40114"
```

```javascript
// olla_dashboard.html
const OLLA_BASE_URL            = "http://192.168.1.182:8080";
const DASHBOARD_BASE           = "http://192.168.1.182:8080";
const REFRESH_INTERVAL_SECONDS = 30;
const LOG_INITIAL_LINES        = 100;
```

## Run

Ad-hoc:
```bash
venv/bin/uvicorn main:app --host 0.0.0.0 --port 8080
```

Via systemd (copy `olla-dashboard.service` to `/etc/systemd/system/`):
```bash
sudo cp olla-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now olla-dashboard.service
```

## Notes

- Runs as `root` so it can read the Olla log file (owned by `olla:olla 0600` on this cluster — adjust `User=` in the service file if your setup differs)
- Olla's routing prefix for Ollama backends: `/olla/ollama/v1/chat/completions`
- CORS is handled by proxying through FastAPI — no changes needed to Olla config
