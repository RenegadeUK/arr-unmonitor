from __future__ import annotations

import os
from datetime import datetime

from flask import Flask, jsonify, redirect, render_template, request, url_for

from .arr_client import ArrClientError, RadarrClient, SonarrClient
from .change_log import ChangeLogStore
from .config import AppSettings, SettingsStore, env
from .poller import ArrPoller


def create_app() -> Flask:
    app = Flask(__name__)

    settings_store = SettingsStore(env("SETTINGS_PATH", "/config/settings.json"))
    change_log_store = ChangeLogStore(env("CHANGE_LOG_PATH", "/config/change-log.jsonl"))
    default_radarr_url = env("RADARR_URL")
    default_radarr_api_key = env("RADARR_API_KEY")
    default_sonarr_url = env("SONARR_URL")
    default_sonarr_api_key = env("SONARR_API_KEY")

    poller = ArrPoller(
        settings_store,
        change_log_store,
        default_radarr_url,
        default_radarr_api_key,
        default_sonarr_url,
        default_sonarr_api_key,
    )
    poller.start()

    def effective_settings(stored: AppSettings) -> AppSettings:
        return AppSettings(
            radarr_url=stored.radarr_url or default_radarr_url,
            radarr_api_key=stored.radarr_api_key or default_radarr_api_key,
            sonarr_url=stored.sonarr_url or default_sonarr_url,
            sonarr_api_key=stored.sonarr_api_key or default_sonarr_api_key,
            radarr_profile_name=stored.radarr_profile_name,
            sonarr_profile_name=stored.sonarr_profile_name,
            radarr_target_quality=stored.radarr_target_quality,
            sonarr_target_quality=stored.sonarr_target_quality,
            radarr_stop_mode=stored.radarr_stop_mode,
            sonarr_stop_mode=stored.sonarr_stop_mode,
            radarr_profile_id=stored.radarr_profile_id,
            sonarr_profile_id=stored.sonarr_profile_id,
            poll_interval_seconds=stored.poll_interval_seconds,
            enabled=stored.enabled,
        )

    def clients_from_settings(settings: AppSettings) -> tuple[RadarrClient, SonarrClient]:
        return (
            RadarrClient(settings.radarr_url, settings.radarr_api_key),
            SonarrClient(settings.sonarr_url, settings.sonarr_api_key),
        )

    @app.route("/", methods=["GET"])
    def index():
        settings = effective_settings(settings_store.load())
        notice = request.args.get("notice", "")
        error = request.args.get("error", "")
        radarr_profiles = []
        sonarr_profiles = []
        profile_error = ""
        radarr_client, sonarr_client = clients_from_settings(settings)

        try:
            radarr_profiles = radarr_client.get_profiles()
        except ArrClientError as exc:
            profile_error = f"Radarr profiles unavailable: {exc}"

        try:
            sonarr_profiles = sonarr_client.get_profiles()
        except ArrClientError as exc:
            profile_error = (
                f"{profile_error} | Sonarr profiles unavailable: {exc}"
                if profile_error
                else f"Sonarr profiles unavailable: {exc}"
            )

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

        recent_changes = []
        for change in change_log_store.recent(200):
            changed_at = change.get("timestamp")
            changed_at_display = str(changed_at)
            if isinstance(changed_at, (float, int)):
                changed_at_display = datetime.fromtimestamp(changed_at).isoformat(
                    sep=" ", timespec="seconds"
                )
            recent_changes.append({**change, "timestamp_display": changed_at_display})

        return render_template(
            "index.html",
            settings=settings,
            radarr_profiles=radarr_profiles,
            sonarr_profiles=sonarr_profiles,
            service_status=poller.stats.service_status,
            profile_error=profile_error,
            notice=notice,
            error=error,
            stats=poller.stats,
            last_run=last_run,
            recent_runs=recent_runs,
            recent_changes=recent_changes,
        )

    @app.post("/settings/radarr")
    def save_radarr_settings():
        current = settings_store.load()
        updated = AppSettings(
            radarr_url=request.form.get("radarr_url", "").strip(),
            radarr_api_key=request.form.get("radarr_api_key", "").strip(),
            sonarr_url=current.sonarr_url,
            sonarr_api_key=current.sonarr_api_key,
            radarr_profile_name=request.form.get("radarr_profile_name", "").strip(),
            sonarr_profile_name=current.sonarr_profile_name,
            radarr_target_quality=request.form.get("radarr_target_quality", "").strip(),
            sonarr_target_quality=current.sonarr_target_quality,
            radarr_stop_mode=request.form.get("radarr_stop_mode", "cutoff").strip() or "cutoff",
            sonarr_stop_mode=current.sonarr_stop_mode,
            radarr_profile_id=current.radarr_profile_id,
            sonarr_profile_id=current.sonarr_profile_id,
            poll_interval_seconds=current.poll_interval_seconds,
            enabled=current.enabled,
        )
        settings_store.save(updated)
        return redirect(url_for("index", notice="Radarr settings saved"))

    @app.post("/settings/sonarr")
    def save_sonarr_settings():
        current = settings_store.load()
        updated = AppSettings(
            radarr_url=current.radarr_url,
            radarr_api_key=current.radarr_api_key,
            sonarr_url=request.form.get("sonarr_url", "").strip(),
            sonarr_api_key=request.form.get("sonarr_api_key", "").strip(),
            radarr_profile_name=current.radarr_profile_name,
            sonarr_profile_name=request.form.get("sonarr_profile_name", "").strip(),
            radarr_target_quality=current.radarr_target_quality,
            sonarr_target_quality=request.form.get("sonarr_target_quality", "").strip(),
            radarr_stop_mode=current.radarr_stop_mode,
            sonarr_stop_mode=request.form.get("sonarr_stop_mode", "cutoff").strip() or "cutoff",
            radarr_profile_id=current.radarr_profile_id,
            sonarr_profile_id=current.sonarr_profile_id,
            poll_interval_seconds=current.poll_interval_seconds,
            enabled=current.enabled,
        )
        settings_store.save(updated)
        return redirect(url_for("index", notice="Sonarr settings saved"))

    @app.post("/settings")
    def save_settings():
        current = settings_store.load()

        updated = AppSettings(
            radarr_url=current.radarr_url,
            radarr_api_key=current.radarr_api_key,
            sonarr_url=current.sonarr_url,
            sonarr_api_key=current.sonarr_api_key,
            radarr_profile_name=current.radarr_profile_name,
            sonarr_profile_name=current.sonarr_profile_name,
            radarr_target_quality=current.radarr_target_quality,
            sonarr_target_quality=current.sonarr_target_quality,
            radarr_stop_mode=current.radarr_stop_mode,
            sonarr_stop_mode=current.sonarr_stop_mode,
            radarr_profile_id=current.radarr_profile_id,
            sonarr_profile_id=current.sonarr_profile_id,
            poll_interval_seconds=max(int(request.form.get("poll_interval_seconds", 300)), 30),
            enabled=request.form.get("enabled") == "on",
        )

        if current != updated:
            settings_store.save(updated)

        return redirect(url_for("index", notice="Settings saved"))

    @app.post("/test/radarr")
    def test_radarr():
        current = settings_store.load()
        updated = AppSettings(
            radarr_url=request.form.get("radarr_url", "").strip(),
            radarr_api_key=request.form.get("radarr_api_key", "").strip(),
            sonarr_url=current.sonarr_url,
            sonarr_api_key=current.sonarr_api_key,
            radarr_profile_name=request.form.get("radarr_profile_name", "").strip(),
            sonarr_profile_name=current.sonarr_profile_name,
            radarr_target_quality=request.form.get("radarr_target_quality", "").strip(),
            sonarr_target_quality=current.sonarr_target_quality,
            radarr_stop_mode=request.form.get("radarr_stop_mode", "cutoff").strip() or "cutoff",
            sonarr_stop_mode=current.sonarr_stop_mode,
            radarr_profile_id=current.radarr_profile_id,
            sonarr_profile_id=current.sonarr_profile_id,
            poll_interval_seconds=current.poll_interval_seconds,
            enabled=current.enabled,
        )
        settings_store.save(updated)

        settings = effective_settings(updated)
        client = RadarrClient(settings.radarr_url, settings.radarr_api_key)
        try:
            count = len(client.get_profiles())
            poller.update_service_status("radarr", True, f"Connected ({count} profiles)")
            return redirect(url_for("index", notice=f"Radarr saved and connected ({count} profiles)"))
        except ArrClientError as exc:
            poller.update_service_status("radarr", False, str(exc))
            return redirect(url_for("index", error=f"Radarr saved but test failed: {exc}"))

    @app.post("/test/sonarr")
    def test_sonarr():
        current = settings_store.load()
        updated = AppSettings(
            radarr_url=current.radarr_url,
            radarr_api_key=current.radarr_api_key,
            sonarr_url=request.form.get("sonarr_url", "").strip(),
            sonarr_api_key=request.form.get("sonarr_api_key", "").strip(),
            radarr_profile_name=current.radarr_profile_name,
            sonarr_profile_name=request.form.get("sonarr_profile_name", "").strip(),
            radarr_target_quality=current.radarr_target_quality,
            sonarr_target_quality=request.form.get("sonarr_target_quality", "").strip(),
            radarr_stop_mode=current.radarr_stop_mode,
            sonarr_stop_mode=request.form.get("sonarr_stop_mode", "cutoff").strip() or "cutoff",
            radarr_profile_id=current.radarr_profile_id,
            sonarr_profile_id=current.sonarr_profile_id,
            poll_interval_seconds=current.poll_interval_seconds,
            enabled=current.enabled,
        )
        settings_store.save(updated)

        settings = effective_settings(updated)
        client = SonarrClient(settings.sonarr_url, settings.sonarr_api_key)
        try:
            count = len(client.get_profiles())
            poller.update_service_status("sonarr", True, f"Connected ({count} profiles)")
            return redirect(url_for("index", notice=f"Sonarr saved and connected ({count} profiles)"))
        except ArrClientError as exc:
            poller.update_service_status("sonarr", False, str(exc))
            return redirect(url_for("index", error=f"Sonarr saved but test failed: {exc}"))

    @app.post("/run-now")
    def run_now():
        poller.run_once()
        return redirect(url_for("index"))

    @app.post("/clear-history")
    def clear_history():
        poller.clear_history()
        return redirect(url_for("index"))

    @app.post("/clear-change-log")
    def clear_change_log():
        change_log_store.clear()
        return redirect(url_for("index", notice="Change log cleared"))

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok"})

    @app.route("/status", methods=["GET"])
    def status():
        settings = effective_settings(settings_store.load())
        payload = poller.status_payload()
        payload["settings"] = {
            "radarr_url": settings.radarr_url,
            "sonarr_url": settings.sonarr_url,
            "radarr_profile_name": settings.radarr_profile_name,
            "sonarr_profile_name": settings.sonarr_profile_name,
            "radarr_target_quality": settings.radarr_target_quality,
            "sonarr_target_quality": settings.sonarr_target_quality,
            "radarr_stop_mode": settings.radarr_stop_mode,
            "sonarr_stop_mode": settings.sonarr_stop_mode,
            "radarr_profile_id": settings.radarr_profile_id,
            "sonarr_profile_id": settings.sonarr_profile_id,
            "poll_interval_seconds": settings.poll_interval_seconds,
            "enabled": settings.enabled,
        }
        return jsonify(payload)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5200")))
