# API Reference

## Web UI

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Dashboard UI |

## Settings Endpoints

All settings endpoints accept `application/x-www-form-urlencoded` form data and redirect back to `/` with a notice or error query parameter.

### POST `/settings/radarr`

Save Radarr connection and quality settings.

| Form Field | Type | Description |
|---|---|---|
| `radarr_url` | string | Radarr base URL (e.g. `http://radarr:7878`) |
| `radarr_api_key` | string | Radarr API key |
| `radarr_target_quality` | string | Quality name substring to match (e.g. `Remux-2160p`) |
| `radarr_profile_name` | string | Optional quality profile name filter (blank = all) |
| `radarr_stop_mode` | string | Stop mode, defaults to `cutoff` |

### POST `/settings/sonarr`

Save Sonarr connection and quality settings.

| Form Field | Type | Description |
|---|---|---|
| `sonarr_url` | string | Sonarr base URL (e.g. `http://sonarr:8989`) |
| `sonarr_api_key` | string | Sonarr API key |
| `sonarr_target_quality` | string | Quality name substring to match (e.g. `Remux-2160p`) |
| `sonarr_profile_name` | string | Optional quality profile name filter (blank = all) |
| `sonarr_stop_mode` | string | Stop mode, defaults to `cutoff` |

### POST `/settings`

Save worker/polling settings.

| Form Field | Type | Description |
|---|---|---|
| `poll_interval_seconds` | int | Seconds between poll cycles (minimum 30, default 300) |
| `enabled` | string | `on` to enable polling, omit to disable |

## Test Endpoints

Save settings and test API connectivity. Redirect back to `/` with success or failure notice.

### POST `/test/radarr`

Same form fields as `POST /settings/radarr`. Saves settings, then attempts to fetch Radarr quality profiles to verify the connection.

### POST `/test/sonarr`

Same form fields as `POST /settings/sonarr`. Saves settings, then attempts to fetch Sonarr quality profiles to verify the connection.

## Action Endpoints

### POST `/run-now`

Triggers an immediate poll cycle in a background thread. No form data required.

### POST `/clear-history`

Clears the in-memory recent runs list (last 25 polls). Does not affect the persisted change log.

### POST `/clear-change-log`

Truncates the `/config/change-log.jsonl` file, removing all persisted unmonitor records.

## Logging Endpoints

### GET `/api/logs`

Returns application log entries from the in-memory ring buffer, filtered by minimum level.

| Query Param | Type | Default | Description |
|---|---|---|---|
| `limit` | int | `200` | Maximum number of entries to return |
| `level` | string | `DEBUG` | Minimum log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

**Response:** JSON array of log entry objects, newest first.

```json
[
  {
    "timestamp": 1741609201.5,
    "level": "INFO",
    "logger": "app.poller",
    "message": "Poll cycle started"
  }
]
```

### POST `/clear-logs`

Clears both the in-memory log buffer and the persistent log file (`/config/app-log.jsonl`). Redirects to `/#logs`.

## Health & Status

### GET `/health`

Simple liveness probe.

**Response:**
```json
{
  "status": "ok"
}
```

### GET `/status`

Full application status, suitable for monitoring and dashboards.

**Response:**
```json
{
  "last_run": 1741609200.123,
  "last_error": "",
  "last_unmonitored": {
    "radarr": 2,
    "sonarr": 5
  },
  "recent_runs": [
    {
      "started_at": 1741609200.0,
      "finished_at": 1741609203.5,
      "duration_seconds": 3.5,
      "radarr_unmonitored": 2,
      "sonarr_unmonitored": 5,
      "error": ""
    }
  ],
  "recent_changes": [
    {
      "timestamp": 1741609201.0,
      "service": "radarr",
      "item_id": 42,
      "title": "Example Movie",
      "profile_id": 1,
      "action": "unmonitor"
    }
  ],
  "service_status": {
    "radarr": {
      "ok": true,
      "message": "Connected (3 profiles)",
      "checked_at": 1741609200.5
    },
    "sonarr": {
      "ok": true,
      "message": "Connected (2 profiles)",
      "checked_at": 1741609200.8
    }
  },
  "settings": {
    "radarr_url": "http://radarr:7878",
    "sonarr_url": "http://sonarr:8989",
    "radarr_profile_name": "",
    "sonarr_profile_name": "",
    "radarr_target_quality": "Remux-2160p",
    "sonarr_target_quality": "Remux-2160p",
    "radarr_stop_mode": "cutoff",
    "sonarr_stop_mode": "cutoff",
    "radarr_profile_id": null,
    "sonarr_profile_id": null,
    "poll_interval_seconds": 300,
    "enabled": true
  },
  "runtime": {
    "is_running": false,
    "next_run_at": 1741609500.123,
    "seconds_until_next_run": 297,
    "health_state": "healthy",
    "unmonitored_today": 7
  }
}
```

**`runtime.health_state` values:**

| Value | Meaning |
|---|---|
| `healthy` | Last poll succeeded with no errors |
| `running` | A poll cycle is currently in progress |
| `paused` | Polling is disabled via the `enabled` toggle |
| `error` | The last poll encountered an error |
