# Development Guide

## Prerequisites

- Python 3.12+
- pip
- Docker & Docker Compose (for containerised runs)
- A running Radarr and/or Sonarr instance (for integration testing)

## Local Setup

```bash
# Clone the repo
git clone https://github.com/RenegadeUK/arr-unmonitor.git
cd arr-unmonitor

# Create a virtual environment
python -m venv .venv

# Activate it
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# Linux/macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Running Locally

Set required environment variables and start the Flask dev server:

```bash
# Windows PowerShell
$env:RADARR_URL = "http://localhost:7878"
$env:RADARR_API_KEY = "your-key"
$env:SONARR_URL = "http://localhost:8989"
$env:SONARR_API_KEY = "your-key"
$env:SETTINGS_PATH = "./config/settings.json"
$env:CHANGE_LOG_PATH = "./config/change-log.jsonl"

python -m app.main
```

```bash
# Linux/macOS
export RADARR_URL="http://localhost:7878"
export RADARR_API_KEY="your-key"
export SONARR_URL="http://localhost:8989"
export SONARR_API_KEY="your-key"
export SETTINGS_PATH="./config/settings.json"
export CHANGE_LOG_PATH="./config/change-log.jsonl"

python -m app.main
```

The app will be available at `http://localhost:5200`.

## Running with Docker

```bash
# Build and start
docker compose up -d --build

# View logs
docker compose logs -f arr-unmonitor

# Stop
docker compose down
```

Edit `docker-compose.yml` to set your API keys before starting.

## Project Structure

```
arr-unmonitor/
├── app/
│   ├── __init__.py          # Package marker (empty)
│   ├── main.py              # Flask app factory, routes, entry point
│   ├── config.py            # AppSettings dataclass, SettingsStore, env helper
│   ├── arr_client.py        # Radarr/Sonarr API clients
│   ├── poller.py            # Background polling engine (ArrPoller)
│   ├── change_log.py        # Persistent JSONL change log
│   ├── static/
│   │   └── favicon.svg      # App favicon
│   └── templates/
│       └── index.html       # Dashboard UI (Jinja2 template)
├── docs/
│   ├── ARCHITECTURE.md      # System architecture & module breakdown
│   ├── API.md               # HTTP endpoint reference
│   ├── CONFIGURATION.md     # Environment variables & settings reference
│   └── DEVELOPMENT.md       # This file
├── .github/
│   └── workflows/
│       └── ghcr.yml         # CI/CD: build & push to GHCR
├── docker-compose.yml       # Docker Compose service definition
├── Dockerfile               # Container image build
├── requirements.txt         # Python dependencies
└── README.md                # Project overview
```

## Key Design Decisions

1. **Flask factory pattern** — `create_app()` allows the app to be imported and configured without side effects.
2. **Daemon poller thread** — The `ArrPoller` runs in a daemon thread so it shuts down cleanly with the process. A threading lock prevents overlapping poll cycles.
3. **JSON Lines change log** — Append-only format for crash resilience and simple tail/grep inspection.
4. **Settings cascade** — Environment variables provide infrastructure-level defaults; the web UI lets users override without redeploying.
5. **Substring quality matching** — Simple and effective; e.g. target `Remux-2160p` matches quality names like `Remux-2160p` or `Remux-2160p Proper`.
6. **Episode-level Sonarr unmonitoring** — Individual episodes are unmonitored rather than entire series, so new episodes continue to be monitored and downloaded.

## Adding a New Feature

1. Create or switch to the `development` branch.
2. Make changes in the relevant module(s).
3. Test locally with `python -m app.main` or `docker compose up --build`.
4. Verify the `/status` JSON endpoint reflects expected behaviour.
5. Commit and push to `development`.
6. Open a PR to `main` when ready.

## Useful Endpoints for Development

| Endpoint | Purpose |
|---|---|
| `http://localhost:5200/` | Web UI |
| `http://localhost:5200/health` | Liveness check |
| `http://localhost:5200/status` | Full JSON status dump |
