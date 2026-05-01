from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


@dataclass(frozen=True)
class DirectoryChangeEvent:
    """Immutable record of a single filesystem diff detected for one monitored directory.

    Attributes:
        root_directory:       Absolute path to the directory that was scanned.
        created:              Relative POSIX paths of files that appeared since
                              the last snapshot.
        modified:             Relative POSIX paths of files whose ``mtime_ns``
                              or size changed since the last snapshot.
        deleted:              Relative POSIX paths of files that disappeared
                              since the last snapshot.
        detected_at_epoch_s:  Unix timestamp (seconds) when the diff was found.
    """

    root_directory: Path
    created: tuple[str, ...]
    modified: tuple[str, ...]
    deleted: tuple[str, ...]
    detected_at_epoch_s: float

    @property
    def has_changes(self) -> bool:
        """Return True when at least one file was created, modified, or deleted."""
        return bool(self.created or self.modified or self.deleted)


# Type alias for subscriber callbacks registered with DirectoryMonitor.
SubscriberCallback = Callable[[DirectoryChangeEvent], None]


class DirectoryMonitor:
    """Poll one or more directories on a background thread and notify subscribers on diffs.

    Detection is purely stat-based: for every regular file under each root
    directory the monitor records ``(mtime_ns, size)`` in a snapshot.  On each
    poll cycle a fresh snapshot is compared against the previous one to produce
    created / modified / deleted sets.  No OS-level inotify or FSEvents
    dependency is required.

    Typical usage::

        monitor = DirectoryMonitor([handler_dir, assets_dir], poll_interval_s=5)
        monitor.subscribe(lambda event: print(event))
        monitor.start()
        # … later …
        monitor.stop()

    Thread safety: all public methods are safe to call from any thread.
    """

    def __init__(self, root_directories: str | Path | Iterable[str | Path], *, poll_interval_s: float = 1.0) -> None:
        """Initialise the monitor.

        Args:
            root_directories: A single path or an iterable of paths to watch.
                              All paths are resolved to absolute form immediately.
            poll_interval_s:  Seconds between poll cycles.  Clamped to a
                              minimum of 0.1 to avoid busy-looping.
        """
        if isinstance(root_directories, (str, Path)):
            roots = [root_directories]
        else:
            roots = list(root_directories)
        if not roots:
            raise ValueError("At least one root directory is required")

        self.root_directories = tuple(Path(root).expanduser().resolve() for root in roots)
        self.poll_interval_s = max(0.1, float(poll_interval_s))
        # Subscriber registry: id → callback.
        self._subscribers: dict[int, SubscriberCallback] = {}
        self._next_subscriber_id = 1
        # Last known snapshot; None means the monitor has not started yet.
        self._snapshot: dict[Path, dict[str, tuple[int, int]]] | None = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Subscriber management
    # ------------------------------------------------------------------

    def subscribe(self, callback: SubscriberCallback) -> int:
        """Register a callback to be invoked on every change event.

        Args:
            callback: Callable that accepts a :class:`DirectoryChangeEvent`.

        Returns:
            An integer subscriber ID that can be passed to :meth:`unsubscribe`.

        Raises:
            TypeError: If ``callback`` is not callable.
        """
        if not callable(callback):
            raise TypeError("callback must be callable")
        with self._lock:
            subscriber_id = self._next_subscriber_id
            self._next_subscriber_id += 1
            self._subscribers[subscriber_id] = callback
        return subscriber_id

    def unsubscribe(self, subscriber_id: int) -> None:
        """Remove a previously registered callback.

        A no-op if ``subscriber_id`` is not currently registered.
        """
        with self._lock:
            self._subscribers.pop(subscriber_id, None)

    def set_poll_interval(self, poll_interval_s: float) -> None:
        """Update the poll interval at runtime.

        The new value takes effect at the start of the next sleep cycle.
        Clamped to a minimum of 0.1 s.
        """
        self.poll_interval_s = max(0.1, float(poll_interval_s))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Take an initial snapshot and launch the background polling thread.

        Raises:
            FileNotFoundError: If any of the monitored directories do not exist.

        Calling ``start()`` on an already-running monitor is a no-op.
        """
        missing_dirs = [root for root in self.root_directories if not root.exists() or not root.is_dir()]
        if missing_dirs:
            raise FileNotFoundError(f"Directories not found: {', '.join(str(path) for path in missing_dirs)}")

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return  # Already running; nothing to do.
            self._snapshot = self._build_snapshot()
            self._stop_event.clear()
            # Daemon thread so it does not block interpreter shutdown.
            self._thread = threading.Thread(target=self._run, name="DirectoryMonitor", daemon=True)
            self._thread.start()

    def stop(self, timeout_s: float = 2.0) -> None:
        """Signal the background thread to stop and wait for it to exit.

        Args:
            timeout_s: Maximum seconds to wait for the thread to join.
                       The call returns even if the thread is still alive after
                       the timeout (best-effort shutdown).
        """
        with self._lock:
            thread = self._thread
            self._thread = None  # Clear before signalling so re-entrant calls are safe.
        if thread is None:
            return
        self._stop_event.set()
        thread.join(timeout=timeout_s)

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def poll_once(self) -> tuple[DirectoryChangeEvent, ...] | None:
        """Perform a single snapshot diff and notify subscribers if changes are found.

        Can be called manually when event-loop-driven polling is preferred over
        the background thread.  Also used internally by the polling loop.

        Returns:
            A tuple of :class:`DirectoryChangeEvent` objects (one per changed
            directory) when diffs are detected, or ``None`` when nothing changed
            or when called before the first snapshot exists.
        """
        with self._lock:
            if self._snapshot is None:
                # First call before start(); seed the snapshot without firing events.
                self._snapshot = self._build_snapshot()
                return None

            old_snapshot = self._snapshot
            new_snapshot = self._build_snapshot()
            events = self._build_events(old_snapshot=old_snapshot, new_snapshot=new_snapshot)
            # Advance snapshot regardless so the next poll compares against now.
            self._snapshot = new_snapshot

        if not events:
            return None

        self._notify(events)
        return tuple(events)

    def _run(self) -> None:
        """Background thread body: sleep for ``poll_interval_s``, then poll."""
        while not self._stop_event.wait(self.poll_interval_s):
            self.poll_once()

    # ------------------------------------------------------------------
    # Snapshot helpers
    # ------------------------------------------------------------------

    def _build_snapshot(self) -> dict[Path, dict[str, tuple[int, int]]]:
        """Walk all root directories and record ``(mtime_ns, size)`` for every file.

        Returns a dict keyed by root directory path.  Each value is a dict
        mapping relative POSIX file paths to their ``(mtime_ns, size)`` tuple.
        Directories that have disappeared between start and this call are
        represented with an empty inner dict to avoid KeyError on diff.
        """
        snapshot: dict[Path, dict[str, tuple[int, int]]] = {}
        for root_directory in self.root_directories:
            directory_snapshot: dict[str, tuple[int, int]] = {}
            if not root_directory.exists() or not root_directory.is_dir():
                # Directory may have been removed after start(); record as empty.
                snapshot[root_directory] = directory_snapshot
                continue

            for path in root_directory.rglob("*"):
                if not path.is_file():
                    continue
                relative = path.relative_to(root_directory).as_posix()
                try:
                    stats = path.stat()
                except FileNotFoundError:
                    # File removed between rglob listing and stat; skip silently.
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
        """Compare two snapshots and produce a :class:`DirectoryChangeEvent` per changed root.

        Only roots that have at least one created, modified, or deleted file
        produce an event; unchanged roots are skipped.

        Args:
            old_snapshot: Snapshot from the previous poll cycle.
            new_snapshot: Freshly built snapshot from the current poll cycle.

        Returns:
            List of change events; may be empty if nothing changed.
        """
        events: list[DirectoryChangeEvent] = []
        for root_directory in self.root_directories:
            old_directory_snapshot = old_snapshot.get(root_directory, {})
            new_directory_snapshot = new_snapshot.get(root_directory, {})

            old_paths = set(old_directory_snapshot.keys())
            new_paths = set(new_directory_snapshot.keys())

            created = sorted(new_paths - old_paths)
            deleted = sorted(old_paths - new_paths)
            # A file is modified when its (mtime_ns, size) tuple changed.
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
        """Deliver each event to every currently registered subscriber.

        A snapshot of the subscriber list is taken under the lock so that
        callbacks are invoked outside the lock, avoiding deadlocks if a
        callback calls :meth:`subscribe` or :meth:`unsubscribe`.
        Exceptions raised by individual callbacks are swallowed so that one
        bad subscriber cannot disrupt the others.
        """
        with self._lock:
            callbacks = list(self._subscribers.values())

        for event in events:
            for callback in callbacks:
                try:
                    callback(event)
                except Exception:
                    # Keep monitoring and notifying other subscribers despite subscriber errors.
                    continue
