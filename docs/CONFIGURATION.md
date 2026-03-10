# Configuration Reference

## Environment Variables

These are read at startup and provide default values. UI-saved settings override them when present.

| Variable | Required | Default | Description |
|---|---|---|---|
| `RADARR_URL` | Yes | — | Radarr instance base URL (e.g. `http://radarr:7878`) |
| `RADARR_API_KEY` | Yes | — | Radarr API key (found in Radarr → Settings → General) |
| `SONARR_URL` | Yes | — | Sonarr instance base URL (e.g. `http://sonarr:8989`) |
| `SONARR_API_KEY` | Yes | — | Sonarr API key (found in Sonarr → Settings → General) |
| `SETTINGS_PATH` | No | `/config/settings.json` | File path for persisted settings |
| `CHANGE_LOG_PATH` | No | `/config/change-log.jsonl` | File path for the change log |
| `LOG_PATH` | No | `/config/app-log.jsonl` | File path for the persistent application log |
| `PORT` | No | `5200` | HTTP listen port |

## Settings File (`settings.json`)

Persisted at the `SETTINGS_PATH` location. Managed automatically through the web UI.

```json
{
  "radarr_url": "http://radarr:7878",
  "radarr_api_key": "your-radarr-api-key",
  "sonarr_url": "http://sonarr:8989",
  "sonarr_api_key": "your-sonarr-api-key",
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
}
```

### Field Reference

| Field | Type | Default | Description |
|---|---|---|---|
| `radarr_url` | string | `""` | Radarr base URL |
| `radarr_api_key` | string | `""` | Radarr API key |
| `sonarr_url` | string | `""` | Sonarr base URL |
| `sonarr_api_key` | string | `""` | Sonarr API key |
| `radarr_profile_name` | string | `""` | Optional Radarr quality profile filter (blank = all profiles) |
| `sonarr_profile_name` | string | `""` | Optional Sonarr quality profile filter (blank = all profiles) |
| `radarr_target_quality` | string | `""` | Quality text to match for Radarr movies (e.g. `Remux-2160p`) |
| `sonarr_target_quality` | string | `""` | Quality text to match for Sonarr episodes (e.g. `Remux-2160p`) |
| `radarr_stop_mode` | string | `"cutoff"` | Radarr stop mode |
| `sonarr_stop_mode` | string | `"cutoff"` | Sonarr stop mode |
| `radarr_profile_id` | int/null | `null` | Radarr quality profile ID (internal use) |
| `sonarr_profile_id` | int/null | `null` | Sonarr quality profile ID (internal use) |
| `poll_interval_seconds` | int | `300` | Seconds between poll cycles (minimum enforced: 30) |
| `enabled` | bool | `true` | Whether the background poller is active |

## Settings Priority

Settings are resolved in this order (highest priority first):

1. **UI-saved values** — Stored in `settings.json` via the web interface.
2. **Environment variables** — Provide defaults when UI values are empty/blank.

The `effective_settings()` function in `main.py` merges these, preferring non-empty saved values.

## Change Log (`change-log.jsonl`)

Append-only JSON Lines file. Each line is a JSON object recording an unmonitor action:

```json
{"timestamp": 1741609201.0, "service": "radarr", "item_id": 42, "title": "Example Movie", "profile_id": 1, "action": "unmonitor"}
{"timestamp": 1741609202.0, "service": "sonarr", "series_id": 10, "item_id": 55, "title": "S01E03 - Pilot", "profile_id": 2, "action": "unmonitor_episode"}
```

### Radarr Entry Fields

| Field | Description |
|---|---|
| `timestamp` | Unix epoch when the action occurred |
| `service` | Always `"radarr"` |
| `item_id` | Radarr movie ID |
| `title` | Movie title |
| `profile_id` | Quality profile ID (or `null`) |
| `action` | Always `"unmonitor"` |

### Sonarr Entry Fields

| Field | Description |
|---|---|
| `timestamp` | Unix epoch when the action occurred |
| `service` | Always `"sonarr"` |
| `series_id` | Sonarr series ID |
| `item_id` | Sonarr episode ID |
| `title` | Formatted as `S01E03 - Episode Title` |
| `profile_id` | Quality profile ID (or `null`) |
| `action` | Always `"unmonitor_episode"` |

## Docker Volumes

| Container Path | Purpose |
|---|---|
| `/config` | Persistent storage for `settings.json` and `change-log.jsonl` |
