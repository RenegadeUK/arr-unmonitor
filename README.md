# arr-unmonitor

Dockerized Python app that:

- Connects to Radarr + Sonarr APIs
- Pulls available quality profiles
- Shows separate Radarr and Sonarr tiles in the UI
- Lets you choose profile levels in each tile
- Polls in the background
- Unmonitors matching completed items automatically

## UI Port

- TCP `5200`

## Persistent Storage

- Mount local storage to `/config`
- App settings are saved in `/config/settings.json`

## Required Environment Variables

- `RADARR_URL` (example: `http://radarr:7878`)
- `RADARR_API_KEY`
- `SONARR_URL` (example: `http://sonarr:8989`)
- `SONARR_API_KEY`

You can also set these in the UI and save them to `/config/settings.json`.
If both are present, saved UI values are used.

## UI Layout

- Dark gray theme
- Separate Radarr and Sonarr tiles
- Each tile has independent Save + Test connectivity actions
- Live connectivity badge per tile (connected/disconnected), refreshed continuously
- Each tile has explicit `Stop at quality text` (example: `Remux-2160p`)

Optional:

- `SETTINGS_PATH` (default `/config/settings.json`)
- `PORT` (default `5200`)

## Run with Docker Compose

1. Edit `docker-compose.yml` and set API keys.
2. Start:

```bash
docker compose up -d --build
```

3. Open UI:

`http://localhost:5200`

## How Matching Works

- Radarr: unmonitor when
  - item is monitored
  - item has a file (`hasFile = true`)
  - current movie file quality name contains configured quality text
- Sonarr: unmonitor when
  - episode is monitored
  - episode has a downloaded episode file
  - that episode file quality name contains configured quality text
  - action is applied at episode level (`monitored=false` for the episode, series remains monitored)

## GHCR Auto Build on Push

Workflow file: `.github/workflows/ghcr.yml`

On every push, GitHub Actions will:

- Build multi-arch image (`linux/amd64`, `linux/arm64`)
- Push to `ghcr.io/<owner>/<repo>`

Image tags include branch/tag refs and commit SHA.

## Troubleshooting

- JSON status endpoint: `/status`
- Includes current runtime settings, last run result, recent run history, and recent item-level changes.
- Use the "Clear history" button in the UI to reset in-memory recent runs.
- Use each tile's "Test" button to verify its URL + API key.

## Change Log

- Every successful unmonitor action is appended to `/config/change-log.jsonl`.
- UI shows a "Change log" table with service, title, profile, action, and timestamp.
- Use "Clear change log" to reset the persisted log file.
