from __future__ import annotations

import json
import threading
import time
from pathlib import Path


class ChangeLogStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, entry: dict[str, object]) -> None:
        enriched = {"timestamp": time.time(), **entry}
        line = json.dumps(enriched, ensure_ascii=False)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as file:
                file.write(line + "\n")

    def recent(self, limit: int = 200) -> list[dict[str, object]]:
        if limit <= 0 or not self.path.exists():
            return []

        with self._lock:
            with self.path.open("r", encoding="utf-8") as file:
                lines = file.readlines()

        entries: list[dict[str, object]] = []
        for line in reversed(lines[-limit:]):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                if isinstance(payload, dict):
                    entries.append(payload)
            except json.JSONDecodeError:
                continue
        return entries

    def clear(self) -> None:
        with self._lock:
            with self.path.open("w", encoding="utf-8"):
                pass
