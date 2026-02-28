from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class AppSettings:
    radarr_url: str = ""
    radarr_api_key: str = ""
    sonarr_url: str = ""
    sonarr_api_key: str = ""
    radarr_profile_name: str = ""
    sonarr_profile_name: str = ""
    radarr_target_quality: str = ""
    sonarr_target_quality: str = ""
    radarr_stop_mode: str = "cutoff"
    sonarr_stop_mode: str = "cutoff"
    radarr_profile_id: int | None = None
    sonarr_profile_id: int | None = None
    poll_interval_seconds: int = 300
    enabled: bool = True


class SettingsStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> AppSettings:
        if not self.path.exists():
            return AppSettings()
        try:
            with self.path.open("r", encoding="utf-8") as file:
                raw = json.load(file)
            return AppSettings(
                radarr_url=str(raw.get("radarr_url", "")).strip(),
                radarr_api_key=str(raw.get("radarr_api_key", "")).strip(),
                sonarr_url=str(raw.get("sonarr_url", "")).strip(),
                sonarr_api_key=str(raw.get("sonarr_api_key", "")).strip(),
                radarr_profile_name=str(raw.get("radarr_profile_name", "")).strip(),
                sonarr_profile_name=str(raw.get("sonarr_profile_name", "")).strip(),
                radarr_target_quality=str(raw.get("radarr_target_quality", "")).strip(),
                sonarr_target_quality=str(raw.get("sonarr_target_quality", "")).strip(),
                radarr_stop_mode=str(raw.get("radarr_stop_mode", "cutoff")).strip() or "cutoff",
                sonarr_stop_mode=str(raw.get("sonarr_stop_mode", "cutoff")).strip() or "cutoff",
                radarr_profile_id=raw.get("radarr_profile_id"),
                sonarr_profile_id=raw.get("sonarr_profile_id"),
                poll_interval_seconds=int(raw.get("poll_interval_seconds", 300)),
                enabled=bool(raw.get("enabled", True)),
            )
        except (json.JSONDecodeError, OSError, ValueError, TypeError):
            return AppSettings()

    def save(self, settings: AppSettings) -> None:
        with self.path.open("w", encoding="utf-8") as file:
            json.dump(asdict(settings), file, indent=2)


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()