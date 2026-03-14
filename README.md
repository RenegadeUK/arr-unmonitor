# arr-unmonitor

Dockerized Python app that automatically unmonitors completed media items in Radarr and Sonarr once they reach a target quality.

## Features

- **Multi-server support** — configure multiple Radarr and Sonarr instances (e.g. Radarr-4K, Radarr-1080p, Sonarr-Anime)
- **Web UI** with three tabs: Dashboard, Settings, and Logs
- **Settings tab** — full server CRUD: add, edit, test connectivity, delete servers via the UI
- **Dashboard** — summary cards per server with live status badges, worker controls, recent runs, and change log
- **Background poller** — configurable interval, per-server enable/disable
- **Quality matching** — unmonitors items whose file quality name contains the configured target text
- **Change log** — persistent JSONL log of every unmonitor action

## UI Port

- TCP `5200` (configurable via `PORT` env var)

## Persistent Storage

Mount a volume to `/config`. The app stores:

| File | Purpose |
|------|---------|
| `/config/settings.json` | All server configs + global settings |
| `/config/change-log.jsonl` | Item-level change log |
| `/config/app-log.jsonl` | Application log |

## Environment Variables

On **first run** with empty settings, the app seeds servers from these env vars:

| Variable | Example | Description |
|----------|---------|-------------|
| `RADARR_URL` | `http://radarr:7878` | Seeds a "Radarr" server entry |
| `RADARR_API_KEY` | `abc123...` | API key for the seeded Radarr |
| `SONARR_URL` | `http://sonarr:8989` | Seeds a "Sonarr" server entry |
| `SONARR_API_KEY` | `def456...` | API key for the seeded Sonarr |

After first run, **manage all servers in the Settings tab**. Env vars are not re-read.

Optional:

| Variable | Default | Description |
|----------|---------|-------------|
| `SETTINGS_PATH` | `/config/settings.json` | Path for persistent settings |
| `CHANGE_LOG_PATH` | `/config/change-log.jsonl` | Path for change log |
| `LOG_PATH` | `/config/app-log.jsonl` | Path for application log |
| `PORT` | `5200` | HTTP listen port |

## Run with Docker Compose

1. Edit `docker-compose.yml` and set API keys for initial seeding.
2. Start:

```bash
docker compose up -d --build
```

3. Open UI: `http://localhost:5200`
4. Go to **Settings** tab to manage servers, adjust quality targets, and configure polling.

## How Matching Works

- **Radarr**: unmonitors a movie when it is monitored, has a file, and the file's quality name contains the target text
- **Sonarr**: unmonitors individual episodes (series stays monitored) when the episode is monitored, has a downloaded file, and that file's quality name contains the target text

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/servers` | List all configured servers |
| `POST` | `/api/servers` | Add a new server |
| `PUT` | `/api/servers/<name>` | Update a server (supports rename) |
| `DELETE` | `/api/servers/<name>` | Delete a server |
| `POST` | `/api/servers/<name>/test` | Test server connectivity |
| `GET/PUT` | `/api/settings` | Global settings (poll interval, enabled) |
| `GET` | `/api/changes` | Recent change log entries |
| `GET` | `/api/logs` | Application log entries (filterable) |
| `GET` | `/status` | Full runtime status (JSON) |
| `GET` | `/health` | Health check |

## Docker Images

Images are published to both **DockerHub** and **GHCR**:

| Registry | Image |
|----------|-------|
| DockerHub | `blackduke/arr-unmonitor` |
| GHCR | `ghcr.io/renegadeuk/arr-unmonitor` |

All builds are multi-arch (`linux/amd64`, `linux/arm64`).

### Image Tags

| Tag | Description |
|-----|-------------|
| `latest` | Latest stable release |
| `1.0.0` / `1.0` / `1` | Specific semver release |
| `development` | Latest development branch build |
| `main` | Latest main branch build |
| `sha-abc1234` | Specific commit build |

## CI/CD Pipeline

### Branch Builds (CI)

Workflow: `.github/workflows/ci.yml`

On every push to any branch, GitHub Actions will:

- Build multi-arch image (`linux/amd64`, `linux/arm64`)
- Push to both DockerHub and GHCR
- Tag with branch name and commit SHA

### Releases

Workflow: `.github/workflows/release.yml`

To create a release:

```bash
git tag v1.0.0
git push --tags
```

This will:

- Build multi-arch image
- Push to both DockerHub and GHCR with semver tags (`1.0.0`, `1.0`, `1`, `latest`)
- Create a GitHub Release with auto-generated release notes

Pre-release tags (e.g. `v1.0.0-rc1`, `v1.0.0-beta1`) are marked as pre-releases on GitHub.

## Troubleshooting

- JSON status endpoint: `/status` — includes runtime state, per-server status, recent runs, and changes
- Use the **Settings** tab to test connectivity per server
- Use "Clear history" / "Clear change log" buttons on the Dashboard
- Application logs viewable in the **Logs** tab with level and source filtering

## Change Log

- Every successful unmonitor action is appended to `/config/change-log.jsonl`
- Dashboard shows a change log table with service, title, quality, action, and timestamp
- Use "Clear change log" to reset the persisted log file
