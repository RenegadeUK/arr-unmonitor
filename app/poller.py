from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field

from .arr_client import ArrClientError, BaseArrClient, RadarrClient, SonarrClient
from .change_log import ChangeLogStore
from .config import AppSettings, ServerConfig, SettingsStore

logger = logging.getLogger(__name__)


@dataclass
class PollStats:
    last_run: float | None = None
    last_error: str = ""
    last_unmonitored: dict[str, int] = field(default_factory=dict)
    recent_runs: deque[dict[str, object]] = field(default_factory=lambda: deque(maxlen=25))
    service_status: dict[str, dict[str, object]] = field(default_factory=dict)


def client_from_server(server: ServerConfig) -> BaseArrClient:
    """Create an appropriate *arr client from a ServerConfig."""
    if server.type == "sonarr":
        return SonarrClient(server.url, server.api_key, label=server.name)
    return RadarrClient(server.url, server.api_key, label=server.name)


class ArrPoller:
    def __init__(
        self,
        settings_store: SettingsStore,
        change_log_store: ChangeLogStore,
    ) -> None:
        self.settings_store = settings_store
        self.change_log_store = change_log_store
        self.stats = PollStats()
        self._run_lock = threading.Lock()
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
        if not self._run_lock.acquire(blocking=False):
            logger.debug("Poll skipped — already running")
            return

        started_at = time.time()
        settings = self.settings_store.load()
        unmonitored_counts: dict[str, int] = {}
        service_errors: list[str] = []

        logger.info(
            "Poll cycle started — interval %ds, %d server(s)",
            settings.poll_interval_seconds,
            len(settings.servers),
        )

        try:
            if not settings.enabled:
                logger.info("Polling is disabled — skipping processing")
                self.stats.last_error = ""
                self.stats.last_unmonitored = {}
            else:
                for server in settings.servers:
                    if not server.enabled:
                        logger.info("Server '%s' is disabled — skipping", server.name)
                        unmonitored_counts[server.name] = 0
                        continue

                    client = client_from_server(server)
                    ok, message = self._check_service(server.name, client)
                    if not ok:
                        service_errors.append(f"{server.name}: {message}")
                        unmonitored_counts[server.name] = 0
                        continue

                    if server.type == "radarr":
                        count, profile_error = self._process_radarr(server, client)
                    elif server.type == "sonarr":
                        count, profile_error = self._process_sonarr(server, client)
                    else:
                        logger.warning("Unknown server type '%s' for '%s'", server.type, server.name)
                        count, profile_error = 0, ""

                    unmonitored_counts[server.name] = count
                    if profile_error:
                        service_errors.append(f"{server.name}: {profile_error}")

                self.stats.last_unmonitored = unmonitored_counts
                self.stats.last_error = " | ".join(service_errors)
        except ArrClientError as exc:
            logger.error("Poll cycle failed — %s", exc)
            self.stats.last_error = str(exc)
            self.stats.last_unmonitored = {}
        except Exception as exc:
            logger.error("Unexpected error during poll: %s", exc, exc_info=True)
            self.stats.last_error = f"Unexpected error: {exc}"
            self.stats.last_unmonitored = {}
        finally:
            self.stats.last_run = time.time()
            total_unmonitored = sum(unmonitored_counts.values())
            self._record_run(started_at, unmonitored_counts, self.stats.last_error)
            duration = round(self.stats.last_run - started_at, 3)
            summary_parts = [f"{name}={count}" for name, count in unmonitored_counts.items()]
            logger.info(
                "Poll cycle completed in %.3fs — unmonitored: %s (total %d)",
                duration, ", ".join(summary_parts) if summary_parts else "none", total_unmonitored,
            )
            self._run_lock.release()

    def _record_run(
        self,
        started_at: float,
        unmonitored_counts: dict[str, int],
        error: str,
    ) -> None:
        self.stats.recent_runs.appendleft(
            {
                "started_at": started_at,
                "finished_at": self.stats.last_run,
                "duration_seconds": round((self.stats.last_run or started_at) - started_at, 3),
                "unmonitored": unmonitored_counts,
                "total_unmonitored": sum(unmonitored_counts.values()),
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

    def is_running(self) -> bool:
        return self._run_lock.locked()

    def is_stopped(self) -> bool:
        return self._stop.is_set()

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
        server: ServerConfig,
        radarr_client: RadarrClient | BaseArrClient,
    ) -> tuple[int, str]:
        target_quality = server.target_quality.strip()
        if not target_quality:
            logger.warning(
                "Target quality text is not set — skipping processing",
                extra={"source_label": radarr_client.label},
            )
            return 0, f"Set target quality text for {server.name}"

        count = 0
        items = radarr_client.get_items()
        monitored_with_file = sum(
            1 for i in items if i.get("monitored") and i.get("hasFile")
        )
        logger.info(
            "Evaluating %d movies (%d monitored with files, target quality: '%s')",
            len(items), monitored_with_file, target_quality,
            extra={"source_label": radarr_client.label},
        )
        for item in items:
            profile_id = item.get(radarr_client.profile_key)
            quality_name = self._radarr_quality_name(item)
            quality_match = self._quality_text_matches(quality_name, target_quality)

            if (
                item.get("monitored", False)
                and item.get("hasFile", False)
                and quality_match
            ):
                title = item.get("title", "Unknown")
                year = item.get("year", "")
                slug = item.get("titleSlug", "")
                movie_url = f"{radarr_client.base_url}/movie/{slug}" if slug else ""
                logger.info(
                    "Unmonitoring movie '%s' (%s) — file quality '%s' matches target '%s'",
                    title, year, quality_name, target_quality,
                    extra={"source_label": radarr_client.label, "link_url": movie_url},
                )
                radarr_client.unmonitor_item(item)
                self._log_change(
                    server.name, item,
                    quality_name=quality_name,
                    link_url=movie_url,
                )
                count += 1
        return count, ""

    def _process_sonarr(
        self,
        server: ServerConfig,
        sonarr_client: SonarrClient | BaseArrClient,
    ) -> tuple[int, str]:
        target_quality = server.target_quality.strip()
        if not target_quality:
            logger.warning(
                "Target quality text is not set — skipping processing",
                extra={"source_label": sonarr_client.label},
            )
            return 0, f"Set target quality text for {server.name}"

        count = 0
        series_list = sonarr_client.get_items()
        logger.info(
            "Evaluating %d series (target quality: '%s')",
            len(series_list), target_quality,
            extra={"source_label": sonarr_client.label},
        )
        for item in series_list:
            series_id = item.get("id")
            if not isinstance(series_id, int):
                continue

            series_title = item.get("title", f"Series {series_id}")
            series_year = item.get("year", "")
            series_slug = item.get("titleSlug", "")
            series_url = f"{sonarr_client.base_url}/series/{series_slug}" if series_slug else ""
            profile_id = item.get(sonarr_client.profile_key)
            episodes = sonarr_client.get_episodes(series_id)
            episode_files = sonarr_client.get_episode_files(series_id)
            monitored_eps = sum(1 for ep in episodes if ep.get("monitored"))
            logger.debug(
                "Scanning '%s' (%s) — %d episodes (%d monitored), %d files",
                series_title, series_year, len(episodes), monitored_eps, len(episode_files),
                extra={"source_label": sonarr_client.label, "link_url": series_url},
            )
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

                ep_title = episode.get("title", "")
                season = episode.get("seasonNumber", "?")
                ep_num = episode.get("episodeNumber", "?")
                logger.info(
                    "Unmonitoring '%s' S%02dE%02d '%s' — file quality '%s' matches target '%s'",
                    series_title,
                    season if isinstance(season, int) else 0,
                    ep_num if isinstance(ep_num, int) else 0,
                    ep_title, quality_name, target_quality,
                    extra={"source_label": sonarr_client.label, "link_url": series_url},
                )
                sonarr_client.unmonitor_episode(episode, series_title=series_title, series_slug=series_slug)
                self._log_sonarr_episode_change(
                    episode, series_id,
                    server_name=server.name,
                    series_title=series_title,
                    quality_name=quality_name,
                    link_url=series_url,
                )
                count += 1
        return count, ""

    def _check_service(self, service: str, client: RadarrClient | SonarrClient | BaseArrClient) -> tuple[bool, str]:
        try:
            profiles = client.get_profiles()
            profile_names = ", ".join(p.name for p in profiles)
            message = f"Connected ({len(profiles)} profiles: {profile_names})"
            logger.info(
                "Health check passed — %s",
                message,
                extra={"source_label": client.label},
            )
            self.update_service_status(service, True, message)
            return True, message
        except ArrClientError as exc:
            message = str(exc)
            logger.error(
                "Health check FAILED — %s",
                message,
                extra={"source_label": client.label},
            )
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
        *,
        server_name: str = "Sonarr",
        series_title: str = "",
        quality_name: str = "",
        link_url: str = "",
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
                "service": server_name,
                "series_title": series_title,
                "item_id": episode.get("id"),
                "title": label,
                "quality": quality_name,
                "action": "Unmonitored episode",
                "link_url": link_url,
            }
        )

    def _log_change(
        self,
        server_name: str,
        item: dict[str, object],
        *,
        quality_name: str = "",
        link_url: str = "",
    ) -> None:
        title = item.get("title") or item.get("sortTitle") or "Unknown"
        year = item.get("year", "")
        display_title = f"{title} ({year})" if year else str(title)
        self.change_log_store.append(
            {
                "service": server_name,
                "item_id": item.get("id"),
                "title": display_title,
                "quality": quality_name,
                "action": "Unmonitored movie",
                "link_url": link_url,
            }
        )
