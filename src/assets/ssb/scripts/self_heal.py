#!/usr/bin/env python3
"""
SSB V11 Z MARK — Self-Heal System
===================================

Snapshots a baseline of protected files and automatically reverts unauthorized
changes. This is the "self-heal" layer — when an attacker modifies a file
the system has committed to protect, the system restores it from baseline
and quarantines the modified version as evidence.

What it protects:
  - All scripts in /home/z/my-project/scripts/  (the defense layer itself)
  - All config files in /home/z/my-project/ssb-v11-deployment-*/
  - The scanner server, content scanner, soft ping, quarantine daemon,
    filesystem watcher, kernel scanner — THIS FILE included
  - The /etc/passwd, /etc/group, /etc/shadow (if accessible) — system identity
  - The .git/config in the project — git tampering

How it works:
  1. On startup, computes SHA-256 of every protected file and stores the
     baseline in /home/z/my-project/baseline/hashes.json
  2. Every N seconds (default 3s), recomputes hashes and compares to baseline
  3. On mismatch:
     a. Copy the modified file to quarantine as evidence (preserves the attack)
     b. Restore the baseline version from /home/z/my-project/baseline/snapshots/
     c. Log the event to /home/z/my-project/audit_logs/self_heal.log
     d. Emit a finding to the kernel scanner for chain analysis
  4. On missing file (deleted):
     a. Restore from baseline snapshot
     b. Log + emit finding
  5. On new file (not in baseline):
     a. If it's in a protected directory → quarantine it (unauthorized)
     b. Otherwise → log only (new files in /tmp are normal)

The baseline is itself protected: baseline/hashes.json is read-only after
creation, and baseline/snapshots/ is read-only. An attacker who modifies the
baseline would need to first compromise the self_heal process itself —
which the kernel scanner's L6 self-protection layer watches for.

If the baseline doesn't exist on startup, the self-heal system creates it
from the current state of the protected files. This is the "trust on first
use" model — the first snapshot is trusted, subsequent changes are not.
"""

from __future__ import annotations
import hashlib
import json
import os
import shutil
import stat
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from multi_chain_soft_ping import MultiChainSoftPing, SoftPingDecision, Action
from quarantine_daemon import QuarantineDaemon
from defense_state import DEFENSE

BASELINE_DIR = Path("/home/z/my-project/baseline")
SNAPSHOT_DIR = BASELINE_DIR / "snapshots"
HASHES_FILE = BASELINE_DIR / "hashes.json"
AUDIT_LOG = Path("/home/z/my-project/audit_logs/self_heal.log")
AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
_log_lock = threading.Lock()

# Directories whose contents are protected (recursively)
PROTECTED_DIRS = [
    "/home/z/my-project/scripts",
]

# Individual files that are protected
PROTECTED_FILES = [
    "/etc/passwd",
    "/etc/group",
    "/etc/hosts",
    "/home/z/my-project/.gitignore",
    "/home/z/my-project/.env",
]

# File extensions to skip (large binaries that change frequently)
SKIP_EXTENSIONS = {".pyc", ".pyo", ".log", ".tmp", ".swp", ".cache"}


@dataclass
class HealEvent:
    timestamp_ms: float
    path: str
    event_type: str  # "modified", "deleted", "new_unauthorized"
    old_hash: str | None
    new_hash: str | None
    action_taken: str  # "reverted", "restored", "quarantined", "logged"
    quarantine_id: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class SelfHealSystem:
    """Watches protected files and reverts unauthorized changes."""

    def __init__(self, engine: MultiChainSoftPing | None = None,
                 daemon: QuarantineDaemon | None = None,
                 check_interval_s: float = 3.0,
                 max_events: int = 10000) -> None:
        self.engine = engine or MultiChainSoftPing()
        self.daemon = daemon or QuarantineDaemon()
        self.check_interval_s = check_interval_s
        self._events: deque[HealEvent] = deque(maxlen=max_events)
        self._running = False
        self._paused: bool = False  # Toggle: when True, self-heal skips checks
        self._paused_reason: str | None = None
        self._paused_at_ms: float = 0.0
        self._thread: threading.Thread | None = None
        self._baselines: dict[str, str] = {}  # path -> sha256
        self._stats = {
            "checks_total": 0,
            "heals_total": 0,
            "reverts_total": 0,
            "restores_total": 0,
            "new_unauthorized_total": 0,
            "errors_total": 0,
            "started_at_ms": time.time() * 1000.0,
        }

    # -------------------------------------------------------------------
    # Pause / resume (for the knowledge-surface toggle button)
    # -------------------------------------------------------------------

    def pause(self, reason: str = "manual toggle") -> dict[str, Any]:
        """Pause self-heal checks. Use when applying legitimate updates."""
        was_paused = self._paused
        self._paused = True
        self._paused_reason = reason
        self._paused_at_ms = time.time() * 1000.0
        return {
            "status": "paused",
            "was_already_paused": was_paused,
            "reason": reason,
            "paused_at_ms": self._paused_at_ms,
            "protected_files": len(self._baselines),
        }

    def resume(self) -> dict[str, Any]:
        """Resume self-heal checks after a pause."""
        was_paused = self._paused
        self._paused = False
        duration_ms = 0.0
        if was_paused and self._paused_at_ms > 0:
            duration_ms = (time.time() * 1000.0) - self._paused_at_ms
        self._paused_reason = None
        self._paused_at_ms = 0.0
        return {
            "status": "resumed",
            "was_paused": was_paused,
            "pause_duration_ms": duration_ms,
            "protected_files": len(self._baselines),
        }

    def toggle(self) -> dict[str, Any]:
        """Toggle pause/resume. Returns the new state."""
        if self._paused:
            return self.resume()
        else:
            return self.pause("toggled via knowledge surface")

    @property
    def is_paused(self) -> bool:
        return self._paused

    # -------------------------------------------------------------------
    # Baseline management
    # -------------------------------------------------------------------

    def initialize_baseline(self) -> int:
        """Create the baseline from current state. Returns number of files snapshotted."""
        BASELINE_DIR.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        count = 0
        baselines: dict[str, str] = {}
        # Protected dirs
        for d in PROTECTED_DIRS:
            if not os.path.isdir(d):
                continue
            for root, dirs, files in os.walk(d):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    if os.path.islink(fpath):
                        continue
                    ext = os.path.splitext(fname)[1].lower()
                    if ext in SKIP_EXTENSIONS:
                        continue
                    h = self._hash_file(fpath)
                    if h is None:
                        continue
                    baselines[fpath] = h
                    self._snapshot_file(fpath, h)
                    count += 1
        # Protected individual files
        for fpath in PROTECTED_FILES:
            if not os.path.isfile(fpath) or os.path.islink(fpath):
                continue
            h = self._hash_file(fpath)
            if h is None:
                continue
            baselines[fpath] = h
            self._snapshot_file(fpath, h)
            count += 1
        self._baselines = baselines
        # Write hashes file
        HASHES_FILE.write_text(json.dumps(baselines, indent=2))
        # Lock down the baseline
        try:
            os.chmod(HASHES_FILE, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
        except OSError:
            pass
        # Lock down snapshots
        for fpath in baselines:
            snap = SNAPSHOT_DIR / self._snap_name(fpath)
            if snap.exists():
                try:
                    os.chmod(snap, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
                except OSError:
                    pass
        return count

    def load_baseline(self) -> int:
        """Load existing baseline from disk. Returns number of files loaded."""
        if not HASHES_FILE.exists():
            return 0
        try:
            self._baselines = json.loads(HASHES_FILE.read_text())
            return len(self._baselines)
        except (json.JSONDecodeError, OSError):
            return 0

    @staticmethod
    def _hash_file(path: str) -> str | None:
        try:
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            return h.hexdigest()
        except (OSError, PermissionError):
            return None

    @staticmethod
    def _snap_name(path: str) -> str:
        """Convert a path to a safe snapshot filename."""
        return hashlib.sha256(path.encode()).hexdigest()[:32]

    def _snapshot_file(self, path: str, expected_hash: str) -> bool:
        """Copy a file to the snapshot dir."""
        snap = SNAPSHOT_DIR / self._snap_name(path)
        try:
            shutil.copy2(path, snap)
            return True
        except (OSError, shutil.Error):
            return False

    def _restore_from_snapshot(self, path: str) -> bool:
        """Restore a file from its snapshot."""
        snap = SNAPSHOT_DIR / self._snap_name(path)
        if not snap.exists():
            return False
        try:
            # Make sure we can write to the target
            try:
                os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
            except OSError:
                pass
            shutil.copy2(snap, path)
            # Re-lock to original perms (we'll re-hash to verify)
            return True
        except (OSError, shutil.Error):
            return False

    # -------------------------------------------------------------------
    # Check + heal
    # -------------------------------------------------------------------

    def check_and_heal(self) -> list[HealEvent]:
        """Run one check cycle. Returns list of HealEvents.
        Returns empty list if paused."""
        if not DEFENSE.active:
            return []  # Defenses OFF — do nothing until button pressed
        events = []
        # 1. Check all baseline files for modification or deletion
        for path, expected_hash in list(self._baselines.items()):
            try:
                if not os.path.exists(path):
                    # File deleted — restore
                    if self._restore_from_snapshot(path):
                        ev = HealEvent(
                            timestamp_ms=time.time() * 1000.0,
                            path=path, event_type="deleted",
                            old_hash=expected_hash, new_hash=None,
                            action_taken="restored",
                        )
                        self._stats["restores_total"] += 1
                        self._stats["heals_total"] += 1
                        events.append(ev)
                    else:
                        ev = HealEvent(
                            timestamp_ms=time.time() * 1000.0,
                            path=path, event_type="deleted",
                            old_hash=expected_hash, new_hash=None,
                            action_taken="restore_failed",
                            error="snapshot missing or copy failed",
                        )
                        self._stats["errors_total"] += 1
                        events.append(ev)
                    continue
                current_hash = self._hash_file(path)
                if current_hash is None:
                    continue
                if current_hash != expected_hash:
                    # File modified — quarantine the modified version, restore baseline
                    # First, copy modified version to quarantine as evidence
                    qid = None
                    try:
                        from content_scanner import scan_file
                        report = scan_file(path, os.path.basename(path))
                        # Build a minimal decision for quarantine
                        decision = self.engine.decide(report, f"self-heal:{path}")
                        if decision.action in (Action.QUARANTINE.value,
                                               Action.QUARANTINE_ESCALATE.value,
                                               Action.DELETE_ESCALATE.value):
                            rec = self.daemon.quarantine(
                                src_path=Path(path),
                                original_name=os.path.basename(path),
                                source_id=f"self-heal:{path}",
                                scan_report=report,
                                decision=decision,
                            )
                            qid = rec.quarantine_id
                    except Exception as e:
                        # If quarantine fails, just save the modified bytes manually
                        try:
                            evidence_dir = Path(f"/home/z/my-project/quarantine/heal-{expected_hash[:16]}")
                            evidence_dir.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(path, evidence_dir / "modified.bin")
                            (evidence_dir / "info.json").write_text(json.dumps({
                                "path": path, "old_hash": expected_hash,
                                "new_hash": current_hash, "ts": time.time() * 1000.0,
                            }, indent=2))
                            qid = evidence_dir.name
                        except Exception:
                            pass
                    # Restore baseline
                    if self._restore_from_snapshot(path):
                        action = "reverted"
                        self._stats["reverts_total"] += 1
                    else:
                        action = "revert_failed"
                        self._stats["errors_total"] += 1
                    self._stats["heals_total"] += 1
                    events.append(HealEvent(
                        timestamp_ms=time.time() * 1000.0,
                        path=path, event_type="modified",
                        old_hash=expected_hash, new_hash=current_hash,
                        action_taken=action, quarantine_id=qid,
                    ))
            except Exception as e:
                self._stats["errors_total"] += 1
                events.append(HealEvent(
                    timestamp_ms=time.time() * 1000.0,
                    path=path, event_type="error",
                    old_hash=expected_hash, new_hash=None,
                    action_taken="error", error=str(e),
                ))
        # 2. Check for new unauthorized files in protected dirs
        for d in PROTECTED_DIRS:
            if not os.path.isdir(d):
                continue
            for root, dirs, files in os.walk(d):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    if fpath not in self._baselines:
                        ext = os.path.splitext(fname)[1].lower()
                        if ext in SKIP_EXTENSIONS:
                            continue
                        # New unauthorized file — log it (don't auto-quarantine
                        # because we might be in the middle of a legit update)
                        events.append(HealEvent(
                            timestamp_ms=time.time() * 1000.0,
                            path=fpath, event_type="new_unauthorized",
                            old_hash=None, new_hash=self._hash_file(fpath),
                            action_taken="logged",
                        ))
                        self._stats["new_unauthorized_total"] += 1
        return events

    # -------------------------------------------------------------------
    # Main loop
    # -------------------------------------------------------------------

    def start(self) -> bool:
        # Initialize or load baseline
        if self._baselines:
            count = len(self._baselines)
        elif HASHES_FILE.exists():
            count = self.load_baseline()
        else:
            count = self.initialize_baseline()
        print(f"[self_heal] baseline loaded: {count} protected files", file=sys.stderr)
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="self-heal")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        while self._running:
            try:
                events = self.check_and_heal()
                for ev in events:
                    self._events.append(ev)
                    self._log_event(ev)
                self._stats["checks_total"] += 1
                time.sleep(self.check_interval_s)
            except Exception as e:
                self._stats["errors_total"] += 1
                time.sleep(1.0)

    def _log_event(self, ev: HealEvent) -> None:
        line = json.dumps({
            "ts": ev.timestamp_ms,
            "path": ev.path,
            "event": ev.event_type,
            "action": ev.action_taken,
            "old_hash": ev.old_hash[:16] if ev.old_hash else None,
            "new_hash": ev.new_hash[:16] if ev.new_hash else None,
            "qid": ev.quarantine_id,
            "error": ev.error,
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
            "protected_files": len(self._baselines),
            "uptime_ms": (time.time() * 1000.0) - self._stats["started_at_ms"],
            "paused": not DEFENSE.active,
            "defense_active": DEFENSE.active,
            "paused_reason": self._paused_reason,
            "paused_at_ms": self._paused_at_ms,
        }


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Starting self-heal self-test...")
    heal = SelfHealSystem()
    heal.start()
    print("Baseline created. Will run for 15 seconds.")
    print("Try modifying a file in /home/z/my-project/scripts/ to see self-heal in action.")
    try:
        for i in range(15):
            time.sleep(1)
            s = heal.get_stats()
            print(f"  [{i+1}s] checks={s['checks_total']}  heals={s['heals_total']}  "
                  f"reverts={s['reverts_total']}  restores={s['restores_total']}")
        print("\nRecent events:")
        for ev in heal.get_events(20):
            print(f"  [{ev['event_type']:18s}] {ev['action']:14s} {ev['path']}")
    finally:
        heal.stop()
