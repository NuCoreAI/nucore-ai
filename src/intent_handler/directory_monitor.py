from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


@dataclass(frozen=True)
class DirectoryChangeEvent:
    """Represents a single detected filesystem diff for a monitored directory."""

    root_directory: Path
    created: tuple[str, ...]
    modified: tuple[str, ...]
    deleted: tuple[str, ...]
    detected_at_epoch_s: float

    @property
    def has_changes(self) -> bool:
        return bool(self.created or self.modified or self.deleted)


SubscriberCallback = Callable[[DirectoryChangeEvent], None]


class DirectoryMonitor:
    """Poll one or more directories and notify subscribers when diffs are detected."""

    def __init__(self, root_directories: str | Path | Iterable[str | Path], *, poll_interval_s: float = 1.0) -> None:
        if isinstance(root_directories, (str, Path)):
            roots = [root_directories]
        else:
            roots = list(root_directories)
        if not roots:
            raise ValueError("At least one root directory is required")

        self.root_directories = tuple(Path(root).expanduser().resolve() for root in roots)
        self.poll_interval_s = max(0.1, float(poll_interval_s))
        self._subscribers: dict[int, SubscriberCallback] = {}
        self._next_subscriber_id = 1
        self._snapshot: dict[Path, dict[str, tuple[int, int]]] | None = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def subscribe(self, callback: SubscriberCallback) -> int:
        if not callable(callback):
            raise TypeError("callback must be callable")
        with self._lock:
            subscriber_id = self._next_subscriber_id
            self._next_subscriber_id += 1
            self._subscribers[subscriber_id] = callback
        return subscriber_id

    def unsubscribe(self, subscriber_id: int) -> None:
        with self._lock:
            self._subscribers.pop(subscriber_id, None)

    def set_poll_interval(self, poll_interval_s: float) -> None:
        self.poll_interval_s = max(0.1, float(poll_interval_s))

    def start(self) -> None:
        missing_dirs = [root for root in self.root_directories if not root.exists() or not root.is_dir()]
        if missing_dirs:
            raise FileNotFoundError(f"Directories not found: {', '.join(str(path) for path in missing_dirs)}")

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._snapshot = self._build_snapshot()
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, name="DirectoryMonitor", daemon=True)
            self._thread.start()

    def stop(self, timeout_s: float = 2.0) -> None:
        with self._lock:
            thread = self._thread
            self._thread = None
        if thread is None:
            return
        self._stop_event.set()
        thread.join(timeout=timeout_s)

    def poll_once(self) -> tuple[DirectoryChangeEvent, ...] | None:
        """Return one or more change events if a diff is found; otherwise return None."""
        with self._lock:
            if self._snapshot is None:
                self._snapshot = self._build_snapshot()
                return None

            old_snapshot = self._snapshot
            new_snapshot = self._build_snapshot()
            events = self._build_events(old_snapshot=old_snapshot, new_snapshot=new_snapshot)
            self._snapshot = new_snapshot

        if not events:
            return None

        self._notify(events)
        return tuple(events)

    def _run(self) -> None:
        while not self._stop_event.wait(self.poll_interval_s):
            self.poll_once()

    def _build_snapshot(self) -> dict[Path, dict[str, tuple[int, int]]]:
        snapshot: dict[Path, dict[str, tuple[int, int]]] = {}
        for root_directory in self.root_directories:
            directory_snapshot: dict[str, tuple[int, int]] = {}
            if not root_directory.exists() or not root_directory.is_dir():
                snapshot[root_directory] = directory_snapshot
                continue

            for path in root_directory.rglob("*"):
                if not path.is_file():
                    continue
                relative = path.relative_to(root_directory).as_posix()
                try:
                    stats = path.stat()
                except FileNotFoundError:
                    continue
                directory_snapshot[relative] = (stats.st_mtime_ns, stats.st_size)

            snapshot[root_directory] = directory_snapshot
        return snapshot

    def _build_events(
        self,
        *,
        old_snapshot: dict[Path, dict[str, tuple[int, int]]],
        new_snapshot: dict[Path, dict[str, tuple[int, int]]],
    ) -> list[DirectoryChangeEvent]:
        events: list[DirectoryChangeEvent] = []
        for root_directory in self.root_directories:
            old_directory_snapshot = old_snapshot.get(root_directory, {})
            new_directory_snapshot = new_snapshot.get(root_directory, {})

            old_paths = set(old_directory_snapshot.keys())
            new_paths = set(new_directory_snapshot.keys())

            created = sorted(new_paths - old_paths)
            deleted = sorted(old_paths - new_paths)
            modified = sorted(
                path
                for path in (old_paths & new_paths)
                if old_directory_snapshot.get(path) != new_directory_snapshot.get(path)
            )

            if not (created or modified or deleted):
                continue

            events.append(
                DirectoryChangeEvent(
                    root_directory=root_directory,
                    created=tuple(created),
                    modified=tuple(modified),
                    deleted=tuple(deleted),
                    detected_at_epoch_s=time.time(),
                )
            )

        return events

    def _notify(self, events: list[DirectoryChangeEvent]) -> None:
        with self._lock:
            callbacks = list(self._subscribers.values())

        for event in events:
            for callback in callbacks:
                try:
                    callback(event)
                except Exception:
                    # Keep monitoring and notifying other subscribers despite subscriber errors.
                    continue
