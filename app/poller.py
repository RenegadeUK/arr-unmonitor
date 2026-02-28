from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field

from .arr_client import ArrClientError, RadarrClient, SonarrClient
from .change_log import ChangeLogStore
from .config import AppSettings, SettingsStore


@dataclass
class PollStats:
    last_run: float | None = None
    last_error: str = ""
    last_unmonitored: dict[str, int] = field(
        default_factory=lambda: {"radarr": 0, "sonarr": 0}
    )
    recent_runs: deque[dict[str, object]] = field(default_factory=lambda: deque(maxlen=25))
    service_status: dict[str, dict[str, object]] = field(
        default_factory=lambda: {
            "radarr": {"ok": None, "message": "Not checked yet", "checked_at": None},
            "sonarr": {"ok": None, "message": "Not checked yet", "checked_at": None},
        }
    )


class ArrPoller:
    def __init__(
        self,
        settings_store: SettingsStore,
        change_log_store: ChangeLogStore,
        default_radarr_url: str,
        default_radarr_api_key: str,
        default_sonarr_url: str,
        default_sonarr_api_key: str,
    ) -> None:
        self.settings_store = settings_store
        self.change_log_store = change_log_store
        self.default_radarr_url = default_radarr_url
        self.default_radarr_api_key = default_radarr_api_key
        self.default_sonarr_url = default_sonarr_url
        self.default_sonarr_api_key = default_sonarr_api_key
        self.stats = PollStats()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def run_once(self) -> None:
        started_at = time.time()
        settings = self.settings_store.load()
        radarr_client = self._radarr_client(settings)
        sonarr_client = self._sonarr_client(settings)

        radarr_ok, radarr_message = self._check_service("radarr", radarr_client)
        sonarr_ok, sonarr_message = self._check_service("sonarr", sonarr_client)

        service_errors: list[str] = []
        if not radarr_ok:
            service_errors.append(f"Radarr: {radarr_message}")
        if not sonarr_ok:
            service_errors.append(f"Sonarr: {sonarr_message}")

        if not settings.enabled:
            self.stats.last_error = ""
            self.stats.last_run = started_at
            self.stats.last_unmonitored = {"radarr": 0, "sonarr": 0}
            self._record_run(started_at, 0, 0, "disabled")
            return

        try:
            radarr_count, radarr_profile_error = self._process_radarr(settings, radarr_client, radarr_ok)
            sonarr_count, sonarr_profile_error = self._process_sonarr(settings, sonarr_client, sonarr_ok)
            self.stats.last_unmonitored = {
                "radarr": radarr_count,
                "sonarr": sonarr_count,
            }
            if radarr_profile_error:
                service_errors.append(f"Radarr: {radarr_profile_error}")
            if sonarr_profile_error:
                service_errors.append(f"Sonarr: {sonarr_profile_error}")
            self.stats.last_error = " | ".join(service_errors)
        except ArrClientError as exc:
            radarr_count = 0
            sonarr_count = 0
            self.stats.last_error = str(exc)
            self.stats.last_unmonitored = {"radarr": 0, "sonarr": 0}
        finally:
            self.stats.last_run = time.time()
            self._record_run(started_at, radarr_count, sonarr_count, self.stats.last_error)

    def _record_run(
        self,
        started_at: float,
        radarr_count: int,
        sonarr_count: int,
        error: str,
    ) -> None:
        self.stats.recent_runs.appendleft(
            {
                "started_at": started_at,
                "finished_at": self.stats.last_run,
                "duration_seconds": round((self.stats.last_run or started_at) - started_at, 3),
                "radarr_unmonitored": radarr_count,
                "sonarr_unmonitored": sonarr_count,
                "error": error,
            }
        )

    def status_payload(self) -> dict[str, object]:
        return {
            "last_run": self.stats.last_run,
            "last_error": self.stats.last_error,
            "last_unmonitored": self.stats.last_unmonitored,
            "recent_runs": list(self.stats.recent_runs),
            "recent_changes": self.change_log_store.recent(200),
            "service_status": self.stats.service_status,
        }

    def clear_history(self) -> None:
        self.stats.recent_runs.clear()

    def update_service_status(self, service: str, ok: bool, message: str) -> None:
        self.stats.service_status[service] = {
            "ok": ok,
            "message": message,
            "checked_at": time.time(),
        }

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            settings = self.settings_store.load()
            interval = max(int(settings.poll_interval_seconds), 30)
            self._stop.wait(interval)

    def _process_radarr(
        self,
        settings: AppSettings,
        radarr_client: RadarrClient,
        radarr_ok: bool,
    ) -> tuple[int, str]:
        if not radarr_ok:
            return 0, ""

        target_quality = settings.radarr_target_quality.strip()
        if not target_quality:
            return 0, "Set Radarr target quality text"

        count = 0
        for item in radarr_client.get_items():
            profile_id = item.get(radarr_client.profile_key)
            quality_name = self._radarr_quality_name(item)
            quality_match = self._quality_text_matches(quality_name, target_quality)

            if (
                item.get("monitored", False)
                and item.get("hasFile", False)
                and quality_match
            ):
                radarr_client.unmonitor_item(item)
                self._log_change("radarr", item, profile_id if isinstance(profile_id, int) else None)
                count += 1
        return count, ""

    def _process_sonarr(
        self,
        settings: AppSettings,
        sonarr_client: SonarrClient,
        sonarr_ok: bool,
    ) -> tuple[int, str]:
        if not sonarr_ok:
            return 0, ""

        target_quality = settings.sonarr_target_quality.strip()
        if not target_quality:
            return 0, "Set Sonarr target quality text"

        count = 0
        for item in sonarr_client.get_items():
            series_id = item.get("id")
            if not isinstance(series_id, int):
                continue

            profile_id = item.get(sonarr_client.profile_key)
            episodes = sonarr_client.get_episodes(series_id)
            episode_files = sonarr_client.get_episode_files(series_id)
            episode_file_by_id: dict[int, dict[str, object]] = {}
            for episode_file in episode_files:
                file_id = episode_file.get("id")
                if isinstance(file_id, int):
                    episode_file_by_id[file_id] = episode_file

            for episode in episodes:
                if not episode.get("monitored", False):
                    continue
                episode_file_id = episode.get("episodeFileId")
                if not isinstance(episode_file_id, int):
                    continue
                episode_file = episode_file_by_id.get(episode_file_id)
                if not episode_file:
                    continue
                quality_name = self._sonarr_episode_quality_name(episode_file)
                if not self._quality_text_matches(quality_name, target_quality):
                    continue

                sonarr_client.unmonitor_episode(episode)
                self._log_sonarr_episode_change(episode, series_id, profile_id if isinstance(profile_id, int) else None)
                count += 1
        return count, ""

    def _radarr_client(self, settings: AppSettings) -> RadarrClient:
        base_url = settings.radarr_url or self.default_radarr_url
        api_key = settings.radarr_api_key or self.default_radarr_api_key
        return RadarrClient(base_url, api_key)

    def _sonarr_client(self, settings: AppSettings) -> SonarrClient:
        base_url = settings.sonarr_url or self.default_sonarr_url
        api_key = settings.sonarr_api_key or self.default_sonarr_api_key
        return SonarrClient(base_url, api_key)

    def _check_service(self, service: str, client: RadarrClient | SonarrClient) -> tuple[bool, str]:
        try:
            profile_count = len(client.get_profiles())
            message = f"Connected ({profile_count} profiles)"
            self.update_service_status(service, True, message)
            return True, message
        except ArrClientError as exc:
            message = str(exc)
            self.update_service_status(service, False, message)
            return False, message

    def _quality_text_matches(self, quality_name: str, target_quality: str) -> bool:
        current = quality_name.strip().casefold()
        target = target_quality.strip().casefold()
        if not current or not target:
            return False
        return target in current

    def _radarr_quality_name(self, item: dict[str, object]) -> str:
        movie_file = item.get("movieFile")
        if not isinstance(movie_file, dict):
            return ""
        quality = movie_file.get("quality")
        if not isinstance(quality, dict):
            return ""
        quality_detail = quality.get("quality")
        if not isinstance(quality_detail, dict):
            return ""
        name = quality_detail.get("name")
        return str(name).strip() if isinstance(name, str) else ""

    def _sonarr_episode_quality_name(self, episode_file: dict[str, object]) -> str:
        quality = episode_file.get("quality")
        if not isinstance(quality, dict):
            return ""
        quality_detail = quality.get("quality")
        if not isinstance(quality_detail, dict):
            return ""
        name = quality_detail.get("name")
        return name.strip() if isinstance(name, str) else ""

    def _log_sonarr_episode_change(
        self,
        episode: dict[str, object],
        series_id: int,
        profile_id: int | None,
    ) -> None:
        season = episode.get("seasonNumber")
        episode_number = episode.get("episodeNumber")
        title = episode.get("title")
        label = f"Series {series_id}"
        if isinstance(season, int) and isinstance(episode_number, int):
            label = f"S{season:02d}E{episode_number:02d}"
        if isinstance(title, str) and title.strip():
            label = f"{label} - {title.strip()}"
        self.change_log_store.append(
            {
                "service": "sonarr",
                "series_id": series_id,
                "item_id": episode.get("id"),
                "title": label,
                "profile_id": profile_id,
                "action": "unmonitor_episode",
            }
        )

    def _log_change(
        self,
        service: str,
        item: dict[str, object],
        profile_id: int | None,
    ) -> None:
        title = item.get("title") or item.get("sortTitle") or "Unknown"
        self.change_log_store.append(
            {
                "service": service,
                "item_id": item.get("id"),
                "title": str(title),
                "profile_id": profile_id,
                "action": "unmonitor",
            }
        )
