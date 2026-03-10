from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

VALID_SERVER_TYPES = ("radarr", "sonarr")


@dataclass
class ServerConfig:
    """Configuration for a single *arr server instance."""

    name: str = ""
    type: str = "radarr"  # "radarr" or "sonarr"
    url: str = ""
    api_key: str = ""
    target_quality: str = ""
    profile_name: str = ""
    stop_mode: str = "cutoff"
    profile_id: int | None = None
    enabled: bool = True


@dataclass
class AppSettings:
    """Application-wide settings including all configured servers."""

    servers: list[ServerConfig] = field(default_factory=list)
    poll_interval_seconds: int = 300
    enabled: bool = True

    def get_servers_by_type(self, server_type: str) -> list[ServerConfig]:
        return [s for s in self.servers if s.type == server_type]

    def get_server_by_name(self, name: str) -> ServerConfig | None:
        for s in self.servers:
            if s.name == name:
                return s
        return None

    def server_names(self) -> list[str]:
        return [s.name for s in self.servers]


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

            # ── New format: has "servers" list ──
            if "servers" in raw:
                return self._load_new(raw)

            # ── Legacy flat format: auto-migrate ──
            return self._migrate_legacy(raw)

        except (json.JSONDecodeError, OSError, ValueError, TypeError) as exc:
            logger.warning("Failed to load settings from %s, using defaults: %s", self.path, exc)
            return AppSettings()

    def _load_new(self, raw: dict) -> AppSettings:
        servers: list[ServerConfig] = []
        for srv in raw.get("servers", []):
            if not isinstance(srv, dict):
                continue
            servers.append(
                ServerConfig(
                    name=str(srv.get("name", "")).strip(),
                    type=str(srv.get("type", "radarr")).strip().lower(),
                    url=str(srv.get("url", "")).strip(),
                    api_key=str(srv.get("api_key", "")).strip(),
                    target_quality=str(srv.get("target_quality", "")).strip(),
                    profile_name=str(srv.get("profile_name", "")).strip(),
                    stop_mode=str(srv.get("stop_mode", "cutoff")).strip() or "cutoff",
                    profile_id=srv.get("profile_id"),
                    enabled=bool(srv.get("enabled", True)),
                )
            )
        return AppSettings(
            servers=servers,
            poll_interval_seconds=int(raw.get("poll_interval_seconds", 300)),
            enabled=bool(raw.get("enabled", True)),
        )

    def _migrate_legacy(self, raw: dict) -> AppSettings:
        """Convert old flat radarr_*/sonarr_* schema → new servers list."""
        logger.info("Migrating legacy settings format to multi-server schema")
        servers: list[ServerConfig] = []
        radarr_url = str(raw.get("radarr_url", "")).strip()
        radarr_key = str(raw.get("radarr_api_key", "")).strip()
        if radarr_url or radarr_key:
            servers.append(
                ServerConfig(
                    name="Radarr",
                    type="radarr",
                    url=radarr_url,
                    api_key=radarr_key,
                    target_quality=str(raw.get("radarr_target_quality", "")).strip(),
                    profile_name=str(raw.get("radarr_profile_name", "")).strip(),
                    stop_mode=str(raw.get("radarr_stop_mode", "cutoff")).strip() or "cutoff",
                    profile_id=raw.get("radarr_profile_id"),
                    enabled=True,
                )
            )
        sonarr_url = str(raw.get("sonarr_url", "")).strip()
        sonarr_key = str(raw.get("sonarr_api_key", "")).strip()
        if sonarr_url or sonarr_key:
            servers.append(
                ServerConfig(
                    name="Sonarr",
                    type="sonarr",
                    url=sonarr_url,
                    api_key=sonarr_key,
                    target_quality=str(raw.get("sonarr_target_quality", "")).strip(),
                    profile_name=str(raw.get("sonarr_profile_name", "")).strip(),
                    stop_mode=str(raw.get("sonarr_stop_mode", "cutoff")).strip() or "cutoff",
                    profile_id=raw.get("sonarr_profile_id"),
                    enabled=True,
                )
            )
        settings = AppSettings(
            servers=servers,
            poll_interval_seconds=int(raw.get("poll_interval_seconds", 300)),
            enabled=bool(raw.get("enabled", True)),
        )
        # Persist the migrated format immediately
        self.save(settings)
        return settings

    def save(self, settings: AppSettings) -> None:
        data = {
            "servers": [asdict(s) for s in settings.servers],
            "poll_interval_seconds": settings.poll_interval_seconds,
            "enabled": settings.enabled,
        }
        with self.path.open("w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)
        logger.info("Settings saved to %s", self.path)


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()