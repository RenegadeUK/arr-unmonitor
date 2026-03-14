from __future__ import annotations

import logging
import os
from datetime import datetime

from flask import Flask, jsonify, redirect, render_template, request, url_for

from .arr_client import ArrClientError, RadarrClient, SonarrClient
from .change_log import ChangeLogStore
from .config import AppSettings, ServerConfig, SettingsStore, env, VALID_SERVER_TYPES
from .log_manager import setup_logging
from .poller import ArrPoller, client_from_server

logger = logging.getLogger(__name__)


def _seed_servers_from_env(settings: AppSettings, settings_store: SettingsStore) -> AppSettings:
    """On first run with no servers, seed from RADARR_*/SONARR_* env vars."""
    if settings.servers:
        return settings

    radarr_url = env("RADARR_URL")
    radarr_key = env("RADARR_API_KEY")
    sonarr_url = env("SONARR_URL")
    sonarr_key = env("SONARR_API_KEY")

    seeded = False
    if radarr_url or radarr_key:
        settings.servers.append(
            ServerConfig(name="Radarr", type="radarr", url=radarr_url, api_key=radarr_key)
        )
        logger.info("Seeded Radarr server from environment variables")
        seeded = True
    if sonarr_url or sonarr_key:
        settings.servers.append(
            ServerConfig(name="Sonarr", type="sonarr", url=sonarr_url, api_key=sonarr_key)
        )
        logger.info("Seeded Sonarr server from environment variables")
        seeded = True

    if seeded:
        settings_store.save(settings)

    return settings


def create_app() -> Flask:
    app = Flask(__name__)

    log_path = env("LOG_PATH", "/config/app-log.jsonl") or None
    log_store = setup_logging(log_path)

    settings_store = SettingsStore(env("SETTINGS_PATH", "/config/settings.json"))
    change_log_store = ChangeLogStore(env("CHANGE_LOG_PATH", "/config/change-log.jsonl"))

    # Seed servers from env vars on first run (empty settings)
    initial_settings = settings_store.load()
    _seed_servers_from_env(initial_settings, settings_store)

    poller = ArrPoller(settings_store, change_log_store)

    # Only start the poller in the actual serving process:
    # - Flask debug/reloader: only the child process has WERKZEUG_RUN_MAIN=true
    # - Production (no reloader): always start
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        poller.start()
        logger.info(
            "Application started — poller running, global_polling_enabled=%s, servers=%d, UI on port %s",
            initial_settings.enabled,
            len(initial_settings.servers),
            os.getenv("PORT", "5200"),
        )

    # ──────────────────────────────────────────────────────
    # Pages
    # ──────────────────────────────────────────────────────

    @app.route("/", methods=["GET"])
    def index():
        settings = settings_store.load()
        notice = request.args.get("notice", "")
        error = request.args.get("error", "")

        last_run = "Never"
        if poller.stats.last_run:
            last_run = datetime.fromtimestamp(poller.stats.last_run).isoformat(sep=" ", timespec="seconds")

        recent_runs = []
        for run in list(poller.stats.recent_runs):
            started_at = run.get("started_at")
            started_at_display = str(started_at)
            if isinstance(started_at, (float, int)):
                started_at_display = datetime.fromtimestamp(started_at).isoformat(
                    sep=" ", timespec="seconds"
                )
            recent_runs.append({**run, "started_at_display": started_at_display})

        return render_template(
            "index.html",
            settings=settings,
            service_status=poller.stats.service_status,
            notice=notice,
            error=error,
            stats=poller.stats,
            last_run=last_run,
            recent_runs=recent_runs,
        )

    # ──────────────────────────────────────────────────────
    # Server CRUD API
    # ──────────────────────────────────────────────────────

    @app.route("/api/servers", methods=["GET"])
    def api_servers():
        settings = settings_store.load()
        servers = []
        for s in settings.servers:
            runner = poller.get_runner(s.name)
            servers.append({
                "name": s.name,
                "type": s.type,
                "url": s.url,
                "api_key": s.api_key[:4] + "***" if len(s.api_key) > 4 else "***",
                "api_key_full": s.api_key,
                "unmonitor_season": s.unmonitor_season,
                "unmonitor_series": s.unmonitor_series,
                "remonitor_ignore_specials": s.remonitor_ignore_specials,
                "enabled": s.enabled,
                "poll_interval_seconds": s.poll_interval_seconds,
                "runner_active": runner is not None and runner.is_alive() if runner else False,
                "status": poller.stats.service_status.get(s.name, {
                    "ok": None, "message": "Not checked yet", "checked_at": None,
                }),
            })
        return jsonify(servers)

    @app.route("/api/servers", methods=["POST"])
    def api_add_server():
        data = request.get_json(silent=True) or {}
        name = str(data.get("name", "")).strip()
        server_type = str(data.get("type", "")).strip().lower()

        if not name:
            return jsonify({"error": "Server name is required"}), 400
        if server_type not in VALID_SERVER_TYPES:
            return jsonify({"error": f"Type must be one of: {', '.join(VALID_SERVER_TYPES)}"}), 400

        settings = settings_store.load()
        if settings.get_server_by_name(name):
            return jsonify({"error": f"A server named '{name}' already exists"}), 409

        raw_interval = data.get("poll_interval_seconds")
        server = ServerConfig(
            name=name,
            type=server_type,
            url=str(data.get("url", "")).strip(),
            api_key=str(data.get("api_key", "")).strip(),
            unmonitor_season=bool(data.get("unmonitor_season", False)),
            unmonitor_series=bool(data.get("unmonitor_series", False)),
            remonitor_ignore_specials=bool(data.get("remonitor_ignore_specials", True)),
            enabled=bool(data.get("enabled", True)),
            poll_interval_seconds=max(int(raw_interval), 30) if raw_interval else None,
        )
        settings.servers.append(server)
        settings_store.save(settings)
        poller.sync_runners()
        logger.info("Server '%s' (%s) added", name, server_type)
        return jsonify({"ok": True, "message": f"Server '{name}' added"}), 201

    @app.route("/api/servers/<name>", methods=["PUT"])
    def api_update_server(name: str):
        data = request.get_json(silent=True) or {}
        settings = settings_store.load()
        server = settings.get_server_by_name(name)
        if not server:
            return jsonify({"error": f"Server '{name}' not found"}), 404

        # Allow renaming
        new_name = str(data.get("name", name)).strip()
        if new_name != name and settings.get_server_by_name(new_name):
            return jsonify({"error": f"A server named '{new_name}' already exists"}), 409

        new_type = str(data.get("type", server.type)).strip().lower()
        if new_type not in VALID_SERVER_TYPES:
            return jsonify({"error": f"Type must be one of: {', '.join(VALID_SERVER_TYPES)}"}), 400

        server.name = new_name
        server.type = new_type
        server.url = str(data.get("url", server.url)).strip()
        server.api_key = str(data.get("api_key", server.api_key)).strip()
        server.unmonitor_season = bool(data.get("unmonitor_season", server.unmonitor_season))
        server.unmonitor_series = bool(data.get("unmonitor_series", server.unmonitor_series))
        server.remonitor_ignore_specials = bool(data.get("remonitor_ignore_specials", server.remonitor_ignore_specials))
        server.enabled = bool(data.get("enabled", server.enabled))
        # Per-server poll interval (null = use global)
        raw_interval = data.get("poll_interval_seconds")
        if raw_interval is not None:
            server.poll_interval_seconds = max(int(raw_interval), 30) if raw_interval else None
        else:
            # Keep existing value if not provided in the request
            pass

        settings_store.save(settings)
        poller.sync_runners()
        logger.info("Server '%s' updated", new_name)
        return jsonify({"ok": True, "message": f"Server '{new_name}' updated"})

    @app.route("/api/servers/<name>", methods=["DELETE"])
    def api_delete_server(name: str):
        settings = settings_store.load()
        server = settings.get_server_by_name(name)
        if not server:
            return jsonify({"error": f"Server '{name}' not found"}), 404

        settings.servers.remove(server)
        settings_store.save(settings)
        poller.sync_runners()
        # Clean up service status
        poller.stats.service_status.pop(name, None)
        poller.stats.last_unmonitored.pop(name, None)
        logger.info("Server '%s' deleted", name)
        return jsonify({"ok": True, "message": f"Server '{name}' deleted"})

    @app.route("/api/servers/<name>/test", methods=["POST"])
    def api_test_server(name: str):
        settings = settings_store.load()
        server = settings.get_server_by_name(name)
        if not server:
            return jsonify({"error": f"Server '{name}' not found"}), 404

        client = client_from_server(server)
        try:
            count = len(client.get_profiles())
            poller.update_service_status(name, True, f"Connected ({count} profiles)")
            return jsonify({"ok": True, "message": f"Connected ({count} profiles)"})
        except ArrClientError as exc:
            poller.update_service_status(name, False, str(exc))
            return jsonify({"ok": False, "message": str(exc)}), 502

    # ──────────────────────────────────────────────────────
    # Global settings
    # ──────────────────────────────────────────────────────

    @app.route("/api/settings", methods=["GET"])
    def api_get_settings():
        settings = settings_store.load()
        return jsonify({
            "poll_interval_seconds": settings.poll_interval_seconds,
            "enabled": settings.enabled,
        })

    @app.route("/api/settings", methods=["PUT"])
    def api_save_settings():
        data = request.get_json(silent=True) or {}
        settings = settings_store.load()

        if "poll_interval_seconds" in data:
            settings.poll_interval_seconds = max(int(data["poll_interval_seconds"]), 30)
        if "enabled" in data:
            settings.enabled = bool(data["enabled"])

        settings_store.save(settings)
        return jsonify({"ok": True, "message": "Settings saved"})

    # ──────────────────────────────────────────────────────
    # Worker actions
    # ──────────────────────────────────────────────────────

    @app.post("/run-now")
    def run_now():
        poller.run_all_adhoc()
        return redirect(url_for("index", notice="Manual run started"))

    @app.post("/run-now/<server_name>")
    def run_now_server(server_name: str):
        ok, msg = poller.run_server_adhoc(server_name)
        if ok:
            return redirect(url_for("index", notice=msg))
        return redirect(url_for("index", error=msg))

    @app.route("/api/servers/<name>/run", methods=["POST"])
    def api_run_server(name: str):
        ok, msg = poller.run_server_adhoc(name)
        if ok:
            return jsonify({"ok": True, "message": msg})
        return jsonify({"ok": False, "error": msg}), 409

    @app.route("/api/run-all", methods=["POST"])
    def api_run_all():
        ok, msg = poller.run_all_adhoc()
        if ok:
            return jsonify({"ok": True, "message": msg})
        return jsonify({"ok": False, "error": msg}), 409

    @app.post("/stop-worker")
    def stop_worker():
        poller.stop()
        logger.info("Worker stopped via UI")
        return redirect(url_for("index", notice="Worker stopped"))

    @app.post("/start-worker")
    def start_worker():
        poller.start()
        logger.info("Worker started via UI")
        return redirect(url_for("index", notice="Worker started"))

    @app.post("/clear-history")
    def clear_history():
        poller.clear_history()
        return redirect(url_for("index"))

    @app.post("/clear-change-log")
    def clear_change_log():
        change_log_store.clear()
        return redirect(url_for("index", notice="Change log cleared"))

    # ──────────────────────────────────────────────────────
    # Re-monitor (dangerous)
    # ──────────────────────────────────────────────────────

    @app.route("/api/servers/<name>/remonitor", methods=["POST"])
    def api_remonitor_server(name: str):
        settings = settings_store.load()
        server = settings.get_server_by_name(name)
        if not server:
            return jsonify({"error": f"Server '{name}' not found"}), 404

        if poller.remonitor_server(name):
            logger.info("Re-monitor triggered for '%s' via UI", name)
            return jsonify({"ok": True, "message": f"Re-monitor started for '{name}'. Server will be auto-disabled after completion."})
        return jsonify({"error": f"No active runner for '{name}'. Enable the server first."}), 409

    @app.post("/remonitor-all")
    def remonitor_all():
        poller.remonitor_all()
        logger.info("Re-monitor triggered for ALL servers via UI")
        return redirect(url_for("index", notice="Re-monitor started for all servers. Servers will be auto-disabled after completion."))

    @app.route("/api/servers/<name>/unmonitor-specials", methods=["POST"])
    def api_unmonitor_specials(name: str):
        settings = settings_store.load()
        server = settings.get_server_by_name(name)
        if not server:
            return jsonify({"error": f"Server '{name}' not found"}), 404
        if server.type != "sonarr":
            return jsonify({"error": "Unmonitor-specials is only available for Sonarr servers"}), 400

        if poller.unmonitor_specials_server(name):
            logger.info("Unmonitor-specials triggered for '%s' via UI", name)
            return jsonify({"ok": True, "message": f"Unmonitor-specials started for '{name}'."})
        return jsonify({"error": f"No active runner for '{name}'. Enable the server first."}), 409

    # ──────────────────────────────────────────────────────
    # Data APIs
    # ──────────────────────────────────────────────────────

    @app.route("/api/changes", methods=["GET"])
    def api_changes():
        limit = request.args.get("limit", 200, type=int)
        return jsonify(change_log_store.recent(limit))

    @app.route("/api/logs", methods=["GET"])
    def api_logs():
        limit = request.args.get("limit", 200, type=int)
        level = request.args.get("level", "DEBUG")
        source = request.args.get("source", "")
        entries = log_store.recent(limit=limit, min_level=level, source=source)
        return jsonify(entries)

    @app.post("/clear-logs")
    def clear_logs():
        log_store.clear()
        logger.info("Application logs cleared")
        return redirect(url_for("index", _anchor="logs"))

    # ──────────────────────────────────────────────────────
    # Health & Status
    # ──────────────────────────────────────────────────────

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok"})

    @app.route("/status", methods=["GET"])
    def status():
        settings = settings_store.load()
        payload = poller.status_payload()
        now = datetime.now().timestamp()
        next_run_at: float | None = None
        seconds_until_next_run: int | None = None
        if settings.enabled:
            # Find the earliest next run across all runners
            for s in settings.servers:
                runner = poller.get_runner(s.name)
                if runner and runner.last_run and s.enabled:
                    runner_interval = runner._effective_interval(settings)
                    runner_next = runner.last_run + runner_interval
                    if next_run_at is None or runner_next < next_run_at:
                        next_run_at = runner_next
            if next_run_at is not None:
                seconds_until_next_run = max(int(next_run_at - now), 0)

        health_state = "healthy"
        if poller.is_stopped():
            health_state = "stopped"
        elif poller.stats.last_error:
            health_state = "error"
        elif poller.is_running():
            health_state = "running"
        elif not settings.enabled:
            health_state = "paused"
        else:
            # Fallback: detect dead runners or overdue polls even if _aggregate_stats missed them
            any_enabled = any(s.enabled for s in settings.servers)
            if any_enabled:
                has_dead = any(
                    not r.is_alive()
                    for name, r in ((s.name, poller.get_runner(s.name)) for s in settings.servers if s.enabled)
                    if r is not None
                )
                if has_dead:
                    health_state = "error"
                elif poller.stats.last_run:
                    # Flag as error if polls are overdue (> 2x the global interval)
                    overdue_threshold = settings.poll_interval_seconds * 2
                    if now - poller.stats.last_run > overdue_threshold:
                        health_state = "error"

        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        unmonitored_today = change_log_store.count_since(today_start)
        unmonitored_today_by_server = change_log_store.count_since_by_server(today_start)

        payload["servers"] = []
        for s in settings.servers:
            runner = poller.get_runner(s.name)
            srv_status = poller.stats.service_status.get(s.name, {
                "ok": None, "message": "Not checked yet", "checked_at": None,
            })
            runner_info = {}
            if runner:
                runner_interval = runner._effective_interval(settings)
                next_run_for_server: float | None = None
                secs_until: int | None = None
                if runner.last_run and s.enabled:
                    next_run_for_server = runner.last_run + runner_interval
                    secs_until = max(int(next_run_for_server - now), 0)
                runner_info = {
                    "runner_active": runner.is_alive(),
                    "runner_running": runner.is_running(),
                    "current_action": runner.current_action,
                    "poll_interval_seconds": runner_interval,
                    "last_run": runner.last_run,
                    "last_error": runner.last_error,
                    "next_run_at": next_run_for_server,
                    "seconds_until_next_run": secs_until,
                }
            payload["servers"].append({
                "name": s.name,
                "type": s.type,
                "enabled": s.enabled,
                "status": srv_status,
                "last_unmonitored": poller.stats.last_unmonitored.get(s.name, 0),
                "unmonitored_today": unmonitored_today_by_server.get(s.name, 0),
                **runner_info,
            })

        # Legacy compat: keep service_status as a flat dict
        payload["service_status"] = poller.stats.service_status

        payload["settings"] = {
            "poll_interval_seconds": settings.poll_interval_seconds,
            "enabled": settings.enabled,
            "server_count": len(settings.servers),
        }
        payload["runtime"] = {
            "is_running": poller.is_running(),
            "worker_stopped": poller.is_stopped(),
            "next_run_at": next_run_at,
            "seconds_until_next_run": seconds_until_next_run,
            "health_state": health_state,
            "unmonitored_today": unmonitored_today,
        }
        return jsonify(payload)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5200")))
