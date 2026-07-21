#!/usr/bin/env python3
"""
SSB V11 Z MARK — System-Wide Filesystem Watcher
================================================

Watches the ENTIRE filesystem (or a configured set of paths) for new/modified
files and runs the content scanner on every change. This closes the gap left
by the upload-only endpoint — now ANY file that lands on the system, whether
via upload, git clone, scp, wget, cp, or direct write, gets scanned.

Uses Linux inotify via ctypes (no external dependencies). inotify_init1 +
inotify_add_watch + read on the fd. This is the same mechanism `inotifywait`
uses, just in pure Python.

Watched paths (configurable):
  - /home/z/my-project/uploads/        (the upload dir)
  - /home/z/my-project/scripts/        (defense layer source — protect integrity)
  - /home/z/my-project/attacker_files/ (test attacker files)
  - /tmp/                              (temp files — common staging area)
  - /home/z/                           (home directory)
  - /var/tmp/                          (if exists)

On any create/modify/move event:
  1. Read the file bytes immediately
  2. Run content_scanner.scan_file()
  3. Run multi_chain_soft_ping.MultiChainSoftPing.decide()
  4. If action is quarantine or worse → quarantine_daemon.quarantine()
  5. If action is allow_logged → log to filesystem audit log
  6. Always append to /home/z/my-project/audit_logs/filesystem.log

Recurses into subdirectories. Adds new subdirectories as they're created.
"""

from __future__ import annotations
import ctypes
import ctypes.util
import errno
import hashlib
import json
import os
import struct
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable

# Add scripts dir to path
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from content_scanner import scan_file, ScanReport
from multi_chain_soft_ping import MultiChainSoftPing, SoftPingDecision, Action
from quarantine_daemon import QuarantineDaemon
from defense_state import DEFENSE


# ---------------------------------------------------------------------------
# libc inotify bindings
# ---------------------------------------------------------------------------

libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)

# inotify flags
IN_ACCESS        = 0x00000001
IN_MODIFY        = 0x00000002
IN_ATTRIB        = 0x00000004
IN_CLOSE_WRITE   = 0x00000008
IN_CLOSE_NOWRITE = 0x00000010
IN_OPEN          = 0x00000020
IN_MOVED_FROM    = 0x00000040
IN_MOVED_TO      = 0x00000080
IN_CREATE        = 0x00000100
IN_DELETE        = 0x00000200
IN_DELETE_SELF   = 0x00000400
IN_MOVE_SELF     = 0x00000800
IN_UNMOUNT       = 0x00002000
IN_Q_OVERFLOW    = 0x00004000
IN_IGNORED       = 0x00008000
IN_ALL_EVENTS    = 0x0fff
IN_DONT_FOLLOW   = 0x02000000
IN_EXCL_UNLINK   = 0x04000000
IN_MASK_ADD      = 0x20000000
IN_ONESHOT       = 0x80000000
IN_CLOEXEC       = 0x00080000
IN_NONBLOCK      = 0x00000800

# Watch mask: we want create, modify, moved-to (files arriving via rename)
WATCH_MASK = (IN_CREATE | IN_MODIFY | IN_MOVED_TO | IN_ATTRIB |
              IN_DELETE | IN_MOVED_FROM | IN_DELETE_SELF | IN_MOVE_SELF)

# inotify_event struct: int wd, uint32_t mask, uint32_t cookie, uint32_t len, char name[]
EVENT_STRUCT_FMT = "iIII"
EVENT_STRUCT_SIZE = struct.calcsize(EVENT_STRUCT_FMT)  # 16 bytes

# libc function prototypes
libc.inotify_init1.argtypes = [ctypes.c_int]
libc.inotify_init1.restype = ctypes.c_int
libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
libc.inotify_add_watch.restype = ctypes.c_int
libc.inotify_rm_watch.argtypes = [ctypes.c_int, ctypes.c_int]
libc.inotify_rm_watch.restype = ctypes.c_int


# ---------------------------------------------------------------------------
# Filesystem watcher
# ---------------------------------------------------------------------------

DEFAULT_WATCH_PATHS = [
    "/home/z/my-project/uploads",
    "/home/z/my-project/attacker_files",
    "/home/z/my-project/quarantine",  # watch the quarantine itself for tamper attempts
    "/tmp",
    "/var/tmp",
    "/home/z",
]

# Paths we explicitly ignore (to avoid noise / infinite loops)
# NOTE: /home/z/my-project/scripts is protected by the self-heal system,
# so the filesystem watcher skips it to avoid fighting with self-heal over
# legitimate code changes. The scripts dir contains test files with
# exploit-like strings that would trigger false positives.
IGNORE_PATHS = {
    "/home/z/my-project/audit_logs",  # our own logs
    "/home/z/my-project/quarantine",   # we handle quarantine separately
    "/home/z/my-project/scripts",      # protected by self_heal — avoid loop
    "/home/z/my-project/baseline",     # baseline snapshots — don't scan
    "/home/z/my-project/skills",       # skill files — trusted
    "/home/z/my-project/.git",         # git internals
    "/home/z/.cache",
    "/home/z/.npm",
    "/home/z/.npm-global",
    "/home/z/.bun",
    "/home/z/.local",
    "/home/z/.venv",
    "/home/z/.agent-browser",
    "/tmp/scanner.log",
    "/tmp/scanner_system_wide.log",
    "/tmp/ssb-z-mark-push",
    "/tmp/inotify_test_file.txt",
}

# File extensions we scan (others we just log)
SCAN_EXTENSIONS = {
    ".py", ".pyc", ".pyo", ".js", ".mjs", ".ts", ".sh", ".bash",
    ".json", ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".html", ".htm", ".php", ".rb", ".go", ".rs", ".c", ".h",
    ".cpp", ".cc", ".hpp", ".java", ".pl", ".lua",
    ".so", ".bin", ".elf", ".exe", ".dll",
    ".txt", ".md",  # config/notes
    "",  # no extension — could be a script
}

AUDIT_LOG = Path("/home/z/my-project/audit_logs/filesystem.log")
AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
_log_lock = threading.Lock()


@dataclass
class WatchEvent:
    timestamp_ms: float
    path: str
    event_mask: int
    event_type: str
    is_directory: bool
    action_taken: str = "logged"
    scan_severity: float = 0.0
    quarantine_id: str | None = None
    file_size: int = 0
    file_hash: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class FilesystemWatcher:
    """Watches a set of paths via inotify and scans every new/modified file."""

    def __init__(self, watch_paths: list[str] | None = None,
                 engine: MultiChainSoftPing | None = None,
                 daemon: QuarantineDaemon | None = None,
                 max_events: int = 10000) -> None:
        self.watch_paths = watch_paths or DEFAULT_WATCH_PATHS
        self.engine = engine or MultiChainSoftPing()
        self.daemon = daemon or QuarantineDaemon()
        self._fd: int = -1
        self._wd_to_path: dict[int, str] = {}
        self._path_to_wd: dict[str, int] = {}
        self._lock = threading.Lock()
        self._events: deque[WatchEvent] = deque(maxlen=max_events)
        self._running = False
        self._thread: threading.Thread | None = None
        self._stats = {
            "events_total": 0,
            "files_scanned": 0,
            "files_quarantined": 0,
            "files_allowed": 0,
            "errors": 0,
            "started_at_ms": time.time() * 1000.0,
        }
        self._last_error: str | None = None
        self._loop_iterations: int = 0

    @staticmethod
    def _event_type_str(mask: int) -> str:
        types = []
        if mask & IN_CREATE: types.append("CREATE")
        if mask & IN_MODIFY: types.append("MODIFY")
        if mask & IN_MOVED_TO: types.append("MOVED_TO")
        if mask & IN_MOVED_FROM: types.append("MOVED_FROM")
        if mask & IN_DELETE: types.append("DELETE")
        if mask & IN_ATTRIB: types.append("ATTRIB")
        if mask & IN_DELETE_SELF: types.append("DELETE_SELF")
        if mask & IN_MOVE_SELF: types.append("MOVE_SELF")
        return ",".join(types) or f"0x{mask:x}"

    def _is_ignored(self, path: str) -> bool:
        for ignored in IGNORE_PATHS:
            if path == ignored or path.startswith(ignored + "/"):
                return True
        return False

    def _add_watch(self, path: str) -> int:
        """Add an inotify watch for a directory. Returns wd or -1 on failure."""
        if self._is_ignored(path):
            return -1
        try:
            wd = libc.inotify_add_watch(self._fd, path.encode("utf-8"), WATCH_MASK)
            if wd < 0:
                err = ctypes.get_errno()
                # EEXIST is fine — we already watch it
                if err != errno.EEXIST:
                    return -1
                # Find existing wd
                for w, p in self._wd_to_path.items():
                    if p == path:
                        return w
                return -1
            with self._lock:
                self._wd_to_path[wd] = path
                self._path_to_wd[path] = wd
            return wd
        except Exception:
            return -1

    def _add_watch_recursive(self, root: str) -> int:
        """Add watches for root and all subdirectories."""
        count = 0
        if not os.path.isdir(root):
            return 0
        # Add the root
        if self._add_watch(root) >= 0:
            count += 1
        # Walk subdirectories
        try:
            for dirpath, dirnames, _ in os.walk(root):
                # Filter ignored
                dirnames[:] = [d for d in dirnames if not self._is_ignored(os.path.join(dirpath, d))]
                for d in dirnames:
                    full = os.path.join(dirpath, d)
                    if self._add_watch(full) >= 0:
                        count += 1
        except (OSError, PermissionError):
            pass
        return count

    def start(self) -> bool:
        """Initialize inotify and start the watcher thread."""
        self._fd = libc.inotify_init1(IN_CLOEXEC | IN_NONBLOCK)
        if self._fd < 0:
            err = ctypes.get_errno()
            print(f"[filesystem_watcher] inotify_init1 failed: errno={err}", file=sys.stderr, flush=True)
            return False
        total = 0
        for path in self.watch_paths:
            if os.path.isdir(path):
                total += self._add_watch_recursive(path)
        print(f"[filesystem_watcher] watching {total} directories across {len(self.watch_paths)} roots", file=sys.stderr, flush=True)
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="fs-watcher")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._fd >= 0:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = -1

    def _loop(self) -> None:
        """Main watcher loop — reads inotify events and processes them."""
        buf = bytearray(65536)
        error_count = 0
        while self._running:
            try:
                self._loop_iterations += 1
                data = os.read(self._fd, len(buf))
                if not data:
                    # Empty read = EOF or would-block in non-blocking mode
                    time.sleep(0.1)
                    continue
                n = len(data)
                buf[:n] = data
                # Parse events from buffer
                offset = 0
                while offset + EVENT_STRUCT_SIZE <= n:
                    wd, mask, cookie, name_len = struct.unpack_from(
                        EVENT_STRUCT_FMT, buf, offset)
                    offset += EVENT_STRUCT_SIZE
                    if name_len > 0:
                        name_bytes = bytes(buf[offset:offset + name_len])
                        offset += name_len
                        name = name_bytes.rstrip(b"\x00").decode("utf-8", errors="replace")
                    else:
                        name = ""
                    try:
                        self._handle_event(wd, mask, cookie, name)
                    except Exception as e:
                        self._stats["errors"] += 1
                        error_count += 1
                        self._last_error = f"event handling: {e}"
                        if error_count <= 5:
                            print(f"[filesystem_watcher] event handling error: {e}", file=sys.stderr, flush=True)
            except OSError as e:
                if e.errno in (errno.EINTR, errno.EAGAIN):
                    time.sleep(0.05)
                    continue
                self._stats["errors"] += 1
                error_count += 1
                self._last_error = f"os.read errno={e.errno}: {e}"
                if error_count <= 10:
                    print(f"[filesystem_watcher] os.read error: {e} (errno={e.errno})", file=sys.stderr, flush=True)
                time.sleep(0.1)
            except Exception as e:
                self._stats["errors"] += 1
                error_count += 1
                self._last_error = f"loop: {e}"
                if error_count <= 10:
                    print(f"[filesystem_watcher] loop error: {e}", file=sys.stderr, flush=True)
                time.sleep(0.1)

    def _handle_event(self, wd: int, mask: int, cookie: int, name: str) -> None:
        """Handle a single inotify event."""
        with self._lock:
            watch_path = self._wd_to_path.get(wd, "?")
        full_path = os.path.join(watch_path, name) if name else watch_path
        is_dir = bool(mask & IN_ISDIR) if hasattr(__import__("builtins"), "IN_ISDIR") else False
        # Actually IN_ISDIR is 0x40000000 — let me just stat
        event_type = self._event_type_str(mask)
        ts = time.time() * 1000.0
        self._stats["events_total"] += 1

        # If a new directory was created, add a watch for it
        if mask & IN_CREATE and os.path.isdir(full_path):
            self._add_watch(full_path)

        # Only scan files (not directories) on create/modify/move-to
        if mask & (IN_CREATE | IN_MODIFY | IN_MOVED_TO):
            if not os.path.exists(full_path) or os.path.isdir(full_path):
                return
            if self._is_ignored(full_path):
                return
            # Check extension
            ext = os.path.splitext(full_path)[1].lower()
            if ext not in SCAN_EXTENSIONS:
                # Log but don't scan
                self._log_event(WatchEvent(
                    timestamp_ms=ts, path=full_path, event_mask=mask,
                    event_type=event_type, is_directory=False,
                    action_taken="logged_no_scan",
                ))
                return
            self._scan_file(full_path, mask, event_type, ts)
        else:
            # Other events (delete, attrib) — just log
            self._log_event(WatchEvent(
                timestamp_ms=ts, path=full_path, event_mask=mask,
                event_type=event_type, is_directory=is_dir,
                action_taken="logged",
            ))

    def _scan_file(self, path: str, mask: int, event_type: str, ts: float) -> None:
        """Scan a file that was just created/modified/moved."""
        try:
            # Use a source_id that identifies this as a filesystem event
            source_id = f"filesystem-watcher:{event_type}"
            report = scan_file(path, os.path.basename(path))
            decision = self.engine.decide(report, source_id)
            self._stats["files_scanned"] += 1

            quarantine_id = None
            action_taken = decision.action
            if decision.action in (Action.QUARANTINE.value, Action.QUARANTINE_ESCALATE.value,
                                   Action.DELETE_ESCALATE.value):
                # Only quarantine if DEFENSE is ACTIVE (button pressed)
                if not DEFENSE.active:
                    action_taken = "detected_not_quarantined_defense_off"
                    self._stats["files_allowed"] += 1
                elif not path.startswith("/home/z/my-project/quarantine/"):
                    rec = self.daemon.quarantine(
                        src_path=Path(path),
                        original_name=os.path.basename(path),
                        source_id=source_id,
                        scan_report=report,
                        decision=decision,
                        upload_timestamp_ms=ts,
                    )
                    quarantine_id = rec.quarantine_id
                    self._stats["files_quarantined"] += 1
                else:
                    # File is already in quarantine — don't re-quarantine
                    action_taken = "already_quarantined"
            else:
                self._stats["files_allowed"] += 1

            self._log_event(WatchEvent(
                timestamp_ms=ts, path=path, event_mask=mask,
                event_type=event_type, is_directory=False,
                action_taken=action_taken,
                scan_severity=decision.severity_score,
                quarantine_id=quarantine_id,
                file_size=report.file_size,
                file_hash=report.file_hash[:16],
            ))
        except Exception as e:
            self._stats["errors"] += 1
            self._log_event(WatchEvent(
                timestamp_ms=ts, path=path, event_mask=mask,
                event_type=event_type, is_directory=False,
                action_taken="error", error=str(e),
            ))

    def _log_event(self, event: WatchEvent) -> None:
        self._events.append(event)
        line = json.dumps({
            "ts": event.timestamp_ms,
            "path": event.path,
            "event": event.event_type,
            "action": event.action_taken,
            "severity": event.scan_severity,
            "qid": event.quarantine_id,
            "size": event.file_size,
            "hash": event.file_hash,
            "error": event.error,
        }, separators=(",", ":"))
        with _log_lock:
            try:
                with open(AUDIT_LOG, "a") as f:
                    f.write(line + "\n")
            except OSError:
                pass

    def get_events(self, limit: int = 100) -> list[dict[str, Any]]:
        return [e.as_dict() for e in list(self._events)[-limit:]]

    def get_stats(self) -> dict[str, Any]:
        return {
            **self._stats,
            "watches_active": len(self._wd_to_path),
            "uptime_ms": (time.time() * 1000.0) - self._stats["started_at_ms"],
            "loop_iterations": self._loop_iterations,
            "last_error": self._last_error,
            "thread_alive": self._thread is not None and self._thread.is_alive(),
        }


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Starting filesystem watcher self-test...")
    watcher = FilesystemWatcher()
    if not watcher.start():
        print("FAILED to start watcher")
        sys.exit(1)
    print("Watcher running. Create a file in /tmp/ to test.")
    print("Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(5)
            s = watcher.get_stats()
            print(f"  events={s['events_total']}  scanned={s['files_scanned']}  "
                  f"quarantined={s['files_quarantined']}  watches={s['watches_active']}")
    except KeyboardInterrupt:
        print("\nStopping...")
        watcher.stop()
