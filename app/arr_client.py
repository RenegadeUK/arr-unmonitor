from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


@dataclass
class Profile:
    id: int
    name: str


class ArrClientError(Exception):
    pass


class BaseArrClient:
    resource_path: str
    profile_path: str
    profile_key: str

    def __init__(self, base_url: str, api_key: str, timeout: int = 20) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"X-Api-Key": self.api_key, "Content-Type": "application/json"}

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        if not self.base_url or not self.api_key:
            raise ArrClientError("Missing ARR base URL or API key")
        url = f"{self.base_url}/api/v3/{path.lstrip('/')}"
        try:
            response = requests.request(
                method,
                url,
                headers=self._headers(),
                timeout=self.timeout,
                **kwargs,
            )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            raise ArrClientError(str(exc)) from exc

    def get_profiles(self) -> list[Profile]:
        response = self._request("GET", self.profile_path)
        payload = response.json()
        profiles: list[Profile] = []
        for item in payload:
            profiles.append(Profile(id=int(item["id"]), name=str(item["name"])))
        return profiles

    def get_items(self) -> list[dict[str, Any]]:
        return self._request("GET", self.resource_path).json()

    def unmonitor_item(self, item: dict[str, Any]) -> None:
        updated = dict(item)
        updated["monitored"] = False
        item_id = updated.get("id")
        if item_id is None:
            return
        self._request("PUT", f"{self.resource_path}/{item_id}", json=updated)


class RadarrClient(BaseArrClient):
    resource_path = "movie"
    profile_path = "qualityprofile"
    profile_key = "qualityProfileId"


class SonarrClient(BaseArrClient):
    resource_path = "series"
    profile_path = "qualityprofile"
    profile_key = "qualityProfileId"

    def get_episode_files(self, series_id: int) -> list[dict[str, Any]]:
        return self._request("GET", "episodefile", params={"seriesId": series_id}).json()

    def get_episodes(self, series_id: int) -> list[dict[str, Any]]:
        return self._request("GET", "episode", params={"seriesId": series_id}).json()

    def unmonitor_episode(self, episode: dict[str, Any]) -> None:
        updated = dict(episode)
        updated["monitored"] = False
        episode_id = updated.get("id")
        if episode_id is None:
            return
        self._request("PUT", f"episode/{episode_id}", json=updated)
