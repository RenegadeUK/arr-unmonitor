# Architecture

## Overview

**arr-unmonitor** is a Dockerized Python/Flask application that automatically unmonitors media items in [Radarr](https://radarr.video/) (movies) and [Sonarr](https://sonarr.tv/) (TV series/episodes) once they reach a configured quality threshold. It runs a background polling loop, exposes a web UI for configuration on port `5200`, and persists settings and change history to disk.

```
┌──────────────────────────────────────────────────────────┐
│                     Docker Container                     │
│                                                          │
│  ┌──────────────┐   ┌────────────┐   ┌───────────────┐  │
│  │  Flask App   │◄──┤  Gunicorn  │   │  ArrPoller    │  │
│  │  (main.py)   │   │  (2 workers│   │  (background  │  │
│  │   Routes &   │   │   WSGI)    │   │   thread)     │  │
│  │   Templates  │   └────────────┘   └──────┬────────┘  │
│  └──────┬───────┘                           │           │
│         │                                    │           │
│         ▼                                    ▼           │
│  ┌──────────────┐                    ┌───────────────┐  │
│  │SettingsStore │◄───────────────────┤ BaseArrClient │  │
│  │ChangeLogStore│                    │ (Radarr/Sonarr│  │
│  └──────┬───────┘                    │  API clients) │  │
│         │                            └──────┬────────┘  │
│         ▼                                    │           │
│  ┌──────────────┐                            │           │
│  │   /config    │                            ▼           │
│  │ settings.json│                    ┌───────────────┐  │
│  │ change-log.  │                    │  Radarr API   │  │
│  │   jsonl      │                    │  Sonarr API   │  │
│  └──────────────┘                    └───────────────┘  │
└──────────────────────────────────────────────────────────┘
```

## Module Breakdown

### `app/main.py` — Flask Application & Routes

The application entry point. `create_app()` is a Flask factory function that:

1. Initialises `SettingsStore` and `ChangeLogStore` from environment or defaults.
2. Creates and starts the `ArrPoller` background thread.
3. Registers all HTTP routes.

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Renders the web UI dashboard (`index.html`) |
| `/settings/radarr` | POST | Saves Radarr connection & quality settings |
| `/settings/sonarr` | POST | Saves Sonarr connection & quality settings |
| `/settings` | POST | Saves Worker settings (poll interval, enabled toggle) |
| `/test/radarr` | POST | Saves Radarr settings and tests API connectivity |
| `/test/sonarr` | POST | Saves Sonarr settings and tests API connectivity |
| `/run-now` | POST | Triggers an immediate poll cycle in a new thread |
| `/clear-history` | POST | Clears in-memory recent run history |
| `/clear-change-log` | POST | Truncates the persisted change log file |
| `/health` | GET | Returns `{"status": "ok"}` — simple liveness probe |
| `/status` | GET | Returns full JSON status (settings, runtime, runs, changes) |

**Settings resolution:** Environment variables provide defaults; UI-saved values in `settings.json` override them when present (via `effective_settings()`).

### `app/config.py` — Configuration & Settings Persistence

- **`AppSettings`** — A `dataclass` holding all configurable fields:
  - Radarr/Sonarr URL, API key, profile name/ID, target quality, stop mode
  - `poll_interval_seconds` (default 300, minimum 30)
  - `enabled` flag
- **`SettingsStore`** — JSON file-based persistence (`/config/settings.json`). Provides `load()` and `save()` with graceful fallback to defaults on parse errors.
- **`env()`** — Helper to read stripped environment variables with defaults.

### `app/arr_client.py` — Radarr & Sonarr API Clients

- **`BaseArrClient`** — Shared HTTP client base using `requests`. Handles auth headers (`X-Api-Key`), URL construction, and error wrapping into `ArrClientError`.
  - `get_profiles()` — Fetches quality profiles.
  - `get_items()` — Fetches all movies (Radarr) or series (Sonarr).
  - `unmonitor_item()` — Sets `monitored=false` via PUT.
- **`RadarrClient`** — Targets `/api/v3/movie` and `/api/v3/qualityprofile`.
- **`SonarrClient`** — Targets `/api/v3/series` and `/api/v3/qualityprofile`. Adds:
  - `get_episode_files(series_id)` — Fetches episode files for a series.
  - `get_episodes(series_id)` — Fetches episodes for a series.
  - `unmonitor_episode(episode)` — Unmonitors a single episode via PUT.

### `app/poller.py` — Background Polling Engine

- **`PollStats`** — Aggregated stats across all server runners: last run timestamp, errors, unmonitor counts, last 25 runs, and per-service connectivity status.
- **`ServerRunner`** — Independent polling thread per configured server. Each runner:
  1. Owns its own `threading.Thread`, `threading.Lock` (run guard), and `threading.Event` (stop signal).
  2. Tracks per-server stats: `last_run`, `last_error`, `last_unmonitored_count`, `recent_runs`, `service_status`.
  3. Loads settings each cycle to determine its own interval (`server.poll_interval_seconds` or global default, minimum 30s).
  4. Checks connectivity (`_check_service()`).
  5. Processes Radarr movies (`_process_radarr()`) or Sonarr episodes (`_process_sonarr()`).
  6. Records the run result and logs each unmonitor action.
  7. Sleeps for its configured interval before repeating.
- **`ArrPoller`** — Coordinator that manages a `dict[str, ServerRunner]` keyed by server name:
  - `start()` / `stop()` — Start or stop all runners.
  - `sync_runners()` — Called when settings change to add/remove/start/stop runners as needed.
  - `run_all()` — Triggers an immediate poll on every active runner (for "Run Now").
  - `run_server(name)` — Triggers an immediate poll on a specific runner.
  - `status_payload()` — Aggregates stats from all runners for the dashboard.

**Failure isolation:** Each server polls independently. A slow or unreachable server does not block other servers.

**Per-server intervals:** Each server can optionally define its own `poll_interval_seconds`. If not set, the global default is used.

**Quality matching** is case-insensitive substring matching (`target in current`).

### `app/change_log.py` — Persistent Change Log

- **`ChangeLogStore`** — Appends JSON lines to `/config/change-log.jsonl`. Each entry is timestamped and includes service, item ID, title, profile ID, and action.
  - `append(entry)` — Thread-safe append.
  - `recent(limit)` — Returns last N entries in reverse chronological order.
  - `clear()` — Truncates the file.
  - `count_since(timestamp)` — Counts entries after a given timestamp (used for "unmonitored today" stat).

### `app/templates/index.html` — Web UI

Single-page dashboard with:
- **Status bar** — Health badge, runner state, next-run countdown, unmonitored-today count.
- **Radarr tile** — URL, API key, target quality text, optional profile filter, Save/Test buttons.
- **Sonarr tile** — Same layout as Radarr but for Sonarr.
- **Worker tile** — Poll interval, enabled checkbox, Run now/Clear history/Clear change log buttons.
- **Recent runs table** — Last 25 poll cycles with duration and counts.
- **Change log table** — Last 200 unmonitor actions.
- **Auto-refresh** — JavaScript polls `/status` every 5 seconds to update badges and countdown.

### `app/static/`

Contains `favicon.svg` only.

## Data Flow

### Poll Cycle

```
ArrPoller (coordinator)
  ├─► sync_runners()                    # Reconcile runners with settings
  │
  ├─► ServerRunner("Radarr")            # Independent thread
  │     └─► _loop()
  │           ├─► SettingsStore.load()   # Read config + own interval
  │           ├─► _check_service()       # GET /api/v3/qualityprofile
  │           ├─► _process_radarr()
  │           │     ├─► RadarrClient.get_items()           # GET /api/v3/movie
  │           │     └─► For each matching movie:
  │           │           ├─► RadarrClient.unmonitor_item() # PUT /api/v3/movie/{id}
  │           │           └─► ChangeLogStore.append()
  │           └─► sleep(server interval or global interval)
  │
  └─► ServerRunner("Sonarr")            # Independent thread
        └─► _loop()
              ├─► SettingsStore.load()
              ├─► _check_service()
              ├─► _process_sonarr()
              │     ├─► SonarrClient.get_items()            # GET /api/v3/series
              │     └─► For each series:
              │           ├─► SonarrClient.get_episodes()    # GET /api/v3/episode
              │           ├─► SonarrClient.get_episode_files()# GET /api/v3/episodefile
              │           └─► For each matching episode:
              │                 ├─► SonarrClient.unmonitor_episode() # PUT /api/v3/episode/{id}
              │                 └─► ChangeLogStore.append()
              └─► sleep(server interval or global interval)
```

### Settings Save Flow

```
Browser POST /settings/radarr
  └─► main.save_radarr_settings()
        ├─► SettingsStore.load()    # Merge with existing Sonarr settings
        └─► SettingsStore.save()    # Write /config/settings.json
```

## Deployment

- **Runtime:** Python 3.12 (slim Docker image)
- **WSGI:** Gunicorn with 2 workers
- **Port:** 5200 (configurable via `PORT` env var)
- **Persistent volume:** `/config` (settings + change log)
- **CI/CD:** GitHub Actions builds multi-arch images (`linux/amd64`, `linux/arm64`) and pushes to GHCR on every push.

## Dependencies

| Package | Version | Purpose |
|---|---|---|
| Flask | 3.1.0 | Web framework & templating |
| Requests | 2.32.3 | HTTP client for Radarr/Sonarr APIs |
| Gunicorn | 23.0.0 | Production WSGI server |
