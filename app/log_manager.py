from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any


LEVEL_NAMES = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_LEVEL_MAP = {name: getattr(logging, name) for name in LEVEL_NAMES}


class LogStore:
    """Thread-safe in-memory ring buffer + optional JSONL file persistence."""

    def __init__(self, path: str | None = None, maxlen: int = 5000) -> None:
        self._buffer: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._file_path: Path | None = None
        if path:
            self._file_path = Path(path)
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            self._load_from_file(maxlen)

    def _load_from_file(self, maxlen: int) -> None:
        """Seed the ring buffer from the last N lines of the log file."""
        if not self._file_path or not self._file_path.exists():
            return
        try:
            with self._file_path.open("r", encoding="utf-8") as fh:
                lines = fh.readlines()
            for line in lines[-maxlen:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if isinstance(entry, dict):
                        self._buffer.append(entry)
                except json.JSONDecodeError:
                    continue
        except OSError:
            pass

    def append(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self._buffer.append(entry)
            if self._file_path:
                try:
                    with self._file_path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                except OSError:
                    pass

    def recent(
        self, limit: int = 200, min_level: str = "DEBUG", source: str = "",
    ) -> list[dict[str, Any]]:
        numeric = _LEVEL_MAP.get(min_level.upper(), logging.DEBUG)
        source_filter = source.strip().lower() if source else ""
        with self._lock:
            entries = list(self._buffer)
        filtered = [
            e for e in entries
            if _LEVEL_MAP.get(e.get("level", "DEBUG"), 0) >= numeric
            and (not source_filter or e.get("source", "").lower() == source_filter)
        ]
        # newest first
        filtered.reverse()
        return filtered[:limit]

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()
            if self._file_path:
                try:
                    with self._file_path.open("w", encoding="utf-8"):
                        pass
                except OSError:
                    pass


class BufferHandler(logging.Handler):
    """Logging handler that pushes formatted records into a LogStore."""

    def __init__(self, log_store: LogStore) -> None:
        super().__init__()
        self.log_store = log_store

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # 1. Explicit source set via extra={"source_label": "Radarr"}
            source = getattr(record, "source_label", "")

            # 2. Derive from logger name: "app.arr_client.Radarr" → "Radarr"
            if not source:
                parts = record.name.rsplit(".", 1)
                if len(parts) == 2 and parts[0] == "app.arr_client":
                    source = parts[1]
                elif record.name in ("app.poller",):
                    source = "Poller"
                elif record.name in ("app.main", "__main__"):
                    source = "App"
                elif record.name == "app.config":
                    source = "Config"
                elif record.name == "app.change_log":
                    source = "ChangeLog"
                else:
                    source = record.name.rsplit(".", 1)[-1]

            entry: dict[str, Any] = {
                "timestamp": record.created,
                "level": record.levelname,
                "logger": record.name,
                "source": source,
                "message": self.format(record),
            }
            # Optional clickable link (e.g. to Radarr/Sonarr page)
            link_url = getattr(record, "link_url", "")
            if link_url:
                entry["link_url"] = link_url
            self.log_store.append(entry)
        except Exception:
            self.handleError(record)


def setup_logging(log_path: str | None = None) -> LogStore:
    """Configure root logger with a BufferHandler (+ file) and return the store."""
    log_store = LogStore(path=log_path, maxlen=5000)
    handler = BufferHandler(log_store)
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)

    # Quiet down noisy libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    return log_store
