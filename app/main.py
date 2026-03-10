from __future__ import annotations

import logging
import os
import threading
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
    poller.start()
    logger.info("Application started — poller running, UI on port %s", os.getenv("PORT", "5200"))

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
            servers.append({
                "name": s.name,
                "type": s.type,
                "url": s.url,
                "api_key": s.api_key[:4] + "***" if len(s.api_key) > 4 else "***",
                "api_key_full": s.api_key,
                "target_quality": s.target_quality,
                "profile_name": s.profile_name,
                "stop_mode": s.stop_mode,
                "profile_id": s.profile_id,
                "enabled": s.enabled,
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

        server = ServerConfig(
            name=name,
            type=server_type,
            url=str(data.get("url", "")).strip(),
            api_key=str(data.get("api_key", "")).strip(),
            target_quality=str(data.get("target_quality", "")).strip(),
            profile_name=str(data.get("profile_name", "")).strip(),
            stop_mode=str(data.get("stop_mode", "cutoff")).strip() or "cutoff",
            enabled=bool(data.get("enabled", True)),
        )
        settings.servers.append(server)
        settings_store.save(settings)
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
        server.target_quality = str(data.get("target_quality", server.target_quality)).strip()
        server.profile_name = str(data.get("profile_name", server.profile_name)).strip()
        server.stop_mode = str(data.get("stop_mode", server.stop_mode)).strip() or "cutoff"
        server.enabled = bool(data.get("enabled", server.enabled))
        # Reset profile_id when profile_name changes
        server.profile_id = None

        settings_store.save(settings)
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
        threading.Thread(target=poller.run_once, daemon=True).start()
        return redirect(url_for("index", notice="Manual run started"))

    @app.post("/clear-history")
    def clear_history():
        poller.clear_history()
        return redirect(url_for("index"))

    @app.post("/clear-change-log")
    def clear_change_log():
        change_log_store.clear()
        return redirect(url_for("index", notice="Change log cleared"))

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
        if settings.enabled and poller.stats.last_run:
            next_run_at = poller.stats.last_run + max(int(settings.poll_interval_seconds), 30)
            seconds_until_next_run = max(int(next_run_at - now), 0)

        health_state = "healthy"
        if poller.stats.last_error:
            health_state = "error"
        elif poller.is_running():
            health_state = "running"
        elif not settings.enabled:
            health_state = "paused"

        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        unmonitored_today = change_log_store.count_since(today_start)

        payload["servers"] = []
        for s in settings.servers:
            srv_status = poller.stats.service_status.get(s.name, {
                "ok": None, "message": "Not checked yet", "checked_at": None,
            })
            payload["servers"].append({
                "name": s.name,
                "type": s.type,
                "enabled": s.enabled,
                "status": srv_status,
                "last_unmonitored": poller.stats.last_unmonitored.get(s.name, 0),
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
