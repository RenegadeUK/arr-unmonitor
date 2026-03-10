from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import requests

@dataclass
class Profile:
    id: int
    name: str


class ArrClientError(Exception):
    pass


def _host_from_url(url: str) -> str:
    """Extract a short hostname from a base URL for log readability."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return parsed.hostname or url
    except Exception:
        return url


class BaseArrClient:
    resource_path: str
    profile_path: str
    profile_key: str
    service_type: str = "arr"  # overridden by subclasses

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: int = 20,
        label: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.label = label or self.service_type.capitalize()
        self.host = _host_from_url(self.base_url)
        # Instance-specific logger: e.g. app.arr_client.Radarr
        self.logger = logging.getLogger(f"{__name__}.{self.label}")
        # Reuse a single Session for connection pooling (keep-alive)
        self._session = requests.Session()
        self._session.headers.update(
            {"X-Api-Key": self.api_key, "Content-Type": "application/json"}
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        if not self.base_url or not self.api_key:
            raise ArrClientError(f"[{self.label}] Missing base URL or API key")
        url = f"{self.base_url}/api/v3/{path.lstrip('/')}"
        try:
            response = self._session.request(
                method,
                url,
                timeout=self.timeout,
                **kwargs,
            )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            self.logger.error("API request failed: %s /api/v3/%s — %s", method, path.lstrip("/"), exc)
            raise ArrClientError(f"[{self.label}] {exc}") from exc

    def get_profiles(self) -> list[Profile]:
        response = self._request("GET", self.profile_path)
        payload = response.json()
        profiles: list[Profile] = []
        for item in payload:
            profiles.append(Profile(id=int(item["id"]), name=str(item["name"])))
        self.logger.info("Loaded %d quality profiles: %s", len(profiles),
                         ", ".join(p.name for p in profiles))
        return profiles

    def get_items(self) -> list[dict[str, Any]]:
        items = self._request("GET", self.resource_path).json()
        self.logger.info("Fetched %d library items", len(items))
        return items

    def unmonitor_item(self, item: dict[str, Any]) -> None:
        updated = dict(item)
        updated["monitored"] = False
        item_id = updated.get("id")
        if item_id is None:
            return
        title = item.get("title", f"ID {item_id}")
        slug = item.get("titleSlug", "")
        link_url = f"{self.base_url}/{self.resource_path}/{slug}" if slug else ""
        self.logger.info(
            "Unmonitoring '%s' (id=%s)",
            title, item_id,
            extra={"link_url": link_url},
        )
        self._request("PUT", f"{self.resource_path}/{item_id}", json=updated)


class RadarrClient(BaseArrClient):
    resource_path = "movie"
    profile_path = "qualityprofile"
    profile_key = "qualityProfileId"
    service_type = "radarr"


class SonarrClient(BaseArrClient):
    resource_path = "series"
    profile_path = "qualityprofile"
    profile_key = "qualityProfileId"
    service_type = "sonarr"

    def get_episode_files(self, series_id: int) -> list[dict[str, Any]]:
        return self._request("GET", "episodefile", params={"seriesId": series_id}).json()

    def get_episodes(self, series_id: int) -> list[dict[str, Any]]:
        return self._request("GET", "episode", params={"seriesId": series_id}).json()

    def unmonitor_episode(self, episode: dict[str, Any], series_title: str = "", series_slug: str = "") -> None:
        updated = dict(episode)
        updated["monitored"] = False
        episode_id = updated.get("id")
        if episode_id is None:
            return
        ep_label = episode.get("title", f"ID {episode_id}")
        season = episode.get("seasonNumber", "?")
        ep_num = episode.get("episodeNumber", "?")
        series_ctx = f"'{series_title}' " if series_title else ""
        link_url = f"{self.base_url}/series/{series_slug}" if series_slug else ""
        self.logger.info(
            "Unmonitoring %sS%02dE%02d '%s' (id=%s)",
            series_ctx,
            season if isinstance(season, int) else 0,
            ep_num if isinstance(ep_num, int) else 0,
            ep_label, episode_id,
            extra={"link_url": link_url},
        )
        self._request("PUT", f"episode/{episode_id}", json=updated)
