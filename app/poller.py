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

MIN_POLL_INTERVAL = 30


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


# ─────────────────────────────────────────────────────────
# ServerRunner — independent polling thread per server
# ─────────────────────────────────────────────────────────

class ServerRunner:
    """Polls a single *arr server on its own thread and schedule."""

    def __init__(
        self,
        server_name: str,
        settings_store: SettingsStore,
        change_log_store: ChangeLogStore,
    ) -> None:
        self.server_name = server_name
        self.settings_store = settings_store
        self.change_log_store = change_log_store

        # Per-runner stats
        self.last_run: float | None = None
        self.last_error: str = ""
        self.last_unmonitored_count: int = 0
        self.recent_runs: deque[dict[str, object]] = deque(maxlen=25)
        self.service_status: dict[str, object] = {}

        self._run_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ── Lifecycle ──

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name=f"runner-{self.server_name}",
        )
        self._thread.start()
        logger.info("Runner started for '%s'", self.server_name)

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        logger.info("Runner stopped for '%s'", self.server_name)

    def is_running(self) -> bool:
        return self._run_lock.locked()

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── Main loop ──

    def _loop(self) -> None:
        while not self._stop.is_set():
            settings = self.settings_store.load()
            if settings.enabled:
                self.run_once()
            else:
                logger.debug(
                    "Global polling disabled — runner '%s' sleeping", self.server_name,
                )
            interval = self._effective_interval(settings)
            self._stop.wait(interval)

    def _effective_interval(self, settings: AppSettings) -> int:
        server = settings.get_server_by_name(self.server_name)
        if server and server.poll_interval_seconds is not None:
            return max(int(server.poll_interval_seconds), MIN_POLL_INTERVAL)
        return max(int(settings.poll_interval_seconds), MIN_POLL_INTERVAL)

    # ── Single poll cycle ──

    def run_once(self) -> None:
        if not self._run_lock.acquire(blocking=False):
            logger.debug("Poll skipped for '%s' — already running", self.server_name)
            return

        started_at = time.time()
        count = 0
        items_checked = 0
        server_type = ""
        error = ""

        try:
            settings = self.settings_store.load()
            server = settings.get_server_by_name(self.server_name)
            if not server:
                error = f"Server '{self.server_name}' not found in settings"
                logger.warning(error)
                return
            if not server.enabled:
                logger.info("Server '%s' is disabled — skipping", self.server_name)
                return

            server_type = server.type
            client = client_from_server(server)
            ok, message = self._check_service(client)
            if not ok:
                error = message
                return

            if server.type == "radarr":
                count, items_checked, profile_error = self._process_radarr(server, client)
            elif server.type == "sonarr":
                count, items_checked, profile_error = self._process_sonarr(server, client)
            else:
                logger.warning("Unknown server type '%s' for '%s'", server.type, server.name)
                count, items_checked, profile_error = 0, 0, ""

            if profile_error:
                error = profile_error

        except ArrClientError as exc:
            error = str(exc)
            logger.error("Poll failed for '%s' — %s", self.server_name, exc)
        except Exception as exc:
            error = f"Unexpected error: {exc}"
            logger.error(
                "Unexpected error polling '%s': %s", self.server_name, exc, exc_info=True,
            )
        finally:
            finished_at = time.time()
            self.last_run = finished_at
            self.last_error = error
            self.last_unmonitored_count = count
            duration = round(finished_at - started_at, 3)

            self.recent_runs.appendleft({
                "started_at": started_at,
                "finished_at": finished_at,
                "duration_seconds": duration,
                "server": self.server_name,
                "server_type": server_type,
                "items_checked": items_checked,
                "unmonitored": count,
                "error": error,
            })

            logger.info(
                "Poll for '%s' completed in %.3fs — unmonitored: %d%s",
                self.server_name, duration, count,
                f" — error: {error}" if error else "",
            )
            self._run_lock.release()

    # ── Service check ──

    def _check_service(
        self, client: RadarrClient | SonarrClient | BaseArrClient,
    ) -> tuple[bool, str]:
        try:
            profiles = client.get_profiles()
            profile_names = ", ".join(p.name for p in profiles)
            message = f"Connected ({len(profiles)} profiles: {profile_names})"
            logger.info(
                "Health check passed — %s",
                message,
                extra={"source_label": client.label},
            )
            self.service_status = {"ok": True, "message": message, "checked_at": time.time()}
            return True, message
        except ArrClientError as exc:
            message = str(exc)
            logger.error(
                "Health check FAILED — %s",
                message,
                extra={"source_label": client.label},
            )
            self.service_status = {"ok": False, "message": message, "checked_at": time.time()}
            return False, message

    # ── Radarr processing ──

    def _process_radarr(
        self,
        server: ServerConfig,
        radarr_client: RadarrClient | BaseArrClient,
    ) -> tuple[int, int, str]:
        target_quality = server.target_quality.strip()
        if not target_quality:
            logger.warning(
                "Target quality text is not set — skipping processing",
                extra={"source_label": radarr_client.label},
            )
            return 0, 0, f"Set target quality text for {server.name}"

        count = 0
        items = radarr_client.get_items()
        items_checked = len(items)
        monitored_with_file = sum(
            1 for i in items if i.get("monitored") and i.get("hasFile")
        )
        logger.info(
            "Evaluating %d movies (%d monitored with files, target quality: '%s')",
            items_checked, monitored_with_file, target_quality,
            extra={"source_label": radarr_client.label},
        )
        for item in items:
            profile_id = item.get(radarr_client.profile_key)
            quality_name = _radarr_quality_name(item)
            quality_match = _quality_text_matches(quality_name, target_quality)

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
                _log_change(
                    self.change_log_store,
                    server.name, item,
                    quality_name=quality_name,
                    link_url=movie_url,
                )
                count += 1
        return count, items_checked, ""

    # ── Sonarr processing ──

    def _process_sonarr(
        self,
        server: ServerConfig,
        sonarr_client: SonarrClient | BaseArrClient,
    ) -> tuple[int, int, str]:
        target_quality = server.target_quality.strip()
        if not target_quality:
            logger.warning(
                "Target quality text is not set — skipping processing",
                extra={"source_label": sonarr_client.label},
            )
            return 0, 0, f"Set target quality text for {server.name}"

        count = 0
        episodes_checked = 0
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
                episodes_checked += 1
                quality_name = _sonarr_episode_quality_name(episode_file)
                if not _quality_text_matches(quality_name, target_quality):
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
                _log_sonarr_episode_change(
                    self.change_log_store,
                    episode, series_id,
                    server_name=server.name,
                    series_title=series_title,
                    quality_name=quality_name,
                    link_url=series_url,
                )
                count += 1
        return count, episodes_checked, ""


# ─────────────────────────────────────────────────────────
# ArrPoller — coordinator managing per-server runners
# ─────────────────────────────────────────────────────────

class ArrPoller:
    def __init__(
        self,
        settings_store: SettingsStore,
        change_log_store: ChangeLogStore,
    ) -> None:
        self.settings_store = settings_store
        self.change_log_store = change_log_store
        self.stats = PollStats()
        self._runners: dict[str, ServerRunner] = {}
        self._stopped = False

    # ── Lifecycle ──

    def start(self) -> None:
        self._stopped = False
        self.sync_runners()
        logger.info("Poller started — %d runner(s)", len(self._runners))

    def stop(self) -> None:
        self._stopped = True
        for runner in self._runners.values():
            runner.stop()
        logger.info("Poller stopped — all runners stopped")

    def sync_runners(self) -> None:
        """Reconcile running runners with current settings.

        - Start runners for new/re-enabled servers.
        - Stop runners for removed/disabled servers.
        - Leave existing runners untouched (they re-read config each cycle).
        """
        if self._stopped:
            return

        settings = self.settings_store.load()
        desired_names: set[str] = set()
        for server in settings.servers:
            if server.enabled:
                desired_names.add(server.name)

        # Stop runners that are no longer needed
        for name in list(self._runners):
            if name not in desired_names:
                self._runners[name].stop()
                del self._runners[name]
                logger.info("Runner removed for '%s'", name)

        # Start runners for servers that don't have one yet
        for name in desired_names:
            if name not in self._runners or not self._runners[name].is_alive():
                runner = ServerRunner(name, self.settings_store, self.change_log_store)
                runner.start()
                self._runners[name] = runner

    # ── Manual triggers ──

    def run_all(self) -> None:
        """Trigger an immediate poll on every active runner."""
        for runner in list(self._runners.values()):
            threading.Thread(target=runner.run_once, daemon=True).start()

    def run_server(self, server_name: str) -> bool:
        """Trigger an immediate poll on a specific runner. Returns False if not found."""
        runner = self._runners.get(server_name)
        if not runner:
            return False
        threading.Thread(target=runner.run_once, daemon=True).start()
        return True

    # ── Aggregated status ──

    def status_payload(self) -> dict[str, object]:
        self._aggregate_stats()
        return {
            "last_run": self.stats.last_run,
            "last_error": self.stats.last_error,
            "last_unmonitored": self.stats.last_unmonitored,
            "recent_runs": list(self.stats.recent_runs),
            "recent_changes": self.change_log_store.recent(200),
            "service_status": self.stats.service_status,
        }

    def _aggregate_stats(self) -> None:
        """Build aggregated stats from all runners."""
        last_run: float | None = None
        errors: list[str] = []
        unmonitored: dict[str, int] = {}
        service_status: dict[str, dict[str, object]] = {}
        all_runs: list[dict[str, object]] = []

        for name, runner in self._runners.items():
            if runner.last_run is not None:
                if last_run is None or runner.last_run > last_run:
                    last_run = runner.last_run
            if runner.last_error:
                errors.append(f"{name}: {runner.last_error}")
            unmonitored[name] = runner.last_unmonitored_count
            if runner.service_status:
                service_status[name] = runner.service_status
            all_runs.extend(runner.recent_runs)

        # Sort all runs by started_at descending, keep last 25
        all_runs.sort(key=lambda r: r.get("started_at", 0), reverse=True)

        self.stats.last_run = last_run
        self.stats.last_error = " | ".join(errors)
        self.stats.last_unmonitored = unmonitored
        self.stats.recent_runs = deque(all_runs[:25], maxlen=25)
        self.stats.service_status = service_status

    def clear_history(self) -> None:
        for runner in self._runners.values():
            runner.recent_runs.clear()
        self.stats.recent_runs.clear()

    def is_running(self) -> bool:
        return any(r.is_running() for r in self._runners.values())

    def is_stopped(self) -> bool:
        return self._stopped

    def update_service_status(self, service: str, ok: bool, message: str) -> None:
        """Update status for a server (used by test endpoint)."""
        runner = self._runners.get(service)
        if runner:
            runner.service_status = {"ok": ok, "message": message, "checked_at": time.time()}
        self.stats.service_status[service] = {
            "ok": ok,
            "message": message,
            "checked_at": time.time(),
        }

    def get_runner(self, server_name: str) -> ServerRunner | None:
        return self._runners.get(server_name)

    @property
    def runner_names(self) -> list[str]:
        return list(self._runners.keys())


# ─────────────────────────────────────────────────────────
# Shared helpers (module-level, used by ServerRunner)
# ─────────────────────────────────────────────────────────

def _quality_text_matches(quality_name: str, target_quality: str) -> bool:
    current = quality_name.strip().casefold()
    target = target_quality.strip().casefold()
    if not current or not target:
        return False
    return target in current


def _radarr_quality_name(item: dict[str, object]) -> str:
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


def _sonarr_episode_quality_name(episode_file: dict[str, object]) -> str:
    quality = episode_file.get("quality")
    if not isinstance(quality, dict):
        return ""
    quality_detail = quality.get("quality")
    if not isinstance(quality_detail, dict):
        return ""
    name = quality_detail.get("name")
    return name.strip() if isinstance(name, str) else ""


def _log_sonarr_episode_change(
    store: ChangeLogStore,
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
    store.append(
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
    store: ChangeLogStore,
    server_name: str,
    item: dict[str, object],
    *,
    quality_name: str = "",
    link_url: str = "",
) -> None:
    title = item.get("title") or item.get("sortTitle") or "Unknown"
    year = item.get("year", "")
    display_title = f"{title} ({year})" if year else str(title)
    store.append(
        {
            "service": server_name,
            "item_id": item.get("id"),
            "title": display_title,
            "quality": quality_name,
            "action": "Unmonitored movie",
            "link_url": link_url,
        }
    )
