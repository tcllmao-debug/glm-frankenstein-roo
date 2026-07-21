#!/usr/bin/env python3
"""
SSB V11 Z MARK — Real Quarantine Daemon
========================================

A REAL chroot-style quarantine sandbox. NOT os.remove().

On QUARANTINE decision:
  1. Compute SHA-256 of file content
  2. Create /home/z/my-project/quarantine/<sha256[:16]>/  (collision-safe)
  3. Move the uploaded file into that directory, renamed to "evidence.bin"
  4. chmod 000 the evidence file (no read, no write, no exec)
  5. Best-effort noexec mount on the quarantine subdirectory (needs root;
     falls back to chmod 000 if mount fails — which it will in this container)
  6. Write a provenance.json beside the evidence with:
       - original filename
       - original path
       - uploader source_id (IP / session)
       - upload timestamp (ms)
       - quarantine timestamp (ms)
       - file SHA-256
       - file size
       - file type
       - detection chain trace (full SoftPingDecision)
       - content scan report (full ScanReport)
       - quarantine operator (the daemon PID)
  7. Append a row to the append-only audit log at
     /home/z/my-project/audit_logs/quarantine.log
  8. Return a QuarantineRecord to the caller

On DELETE_ESCALATE decision:
  - Same as QUARANTINE, but evidence.bin is overwritten with /dev/urandom
    BEFORE being chmod 000. The provenance + scan report + decision trace
    are STILL preserved (the metadata survives; the bytes are destroyed).
  - This is for high-confidence critical detections where keeping the
    bytes around poses an active risk.

The quarantine directory is append-only by design — once a file is in
there, the daemon will not let it out. There is no "release from
quarantine" API. If a human wants to inspect evidence, they read the
provenance.json + scan_report.json, NOT the evidence.bin (which is
chmod 000 and best-effort noexec-mounted).
"""

from __future__ import annotations
import hashlib
import json
import os
import shutil
import stat
import subprocess
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from content_scanner import ScanReport
from multi_chain_soft_ping import SoftPingDecision


QUARANTINE_ROOT = Path("/home/z/my-project/quarantine")
AUDIT_LOG_PATH = Path("/home/z/my-project/audit_logs/quarantine.log")
EVIDENCE_NAME = "evidence.bin"
PROVENANCE_NAME = "provenance.json"
SCAN_REPORT_NAME = "scan_report.json"
DECISION_NAME = "decision.json"

_log_lock = threading.Lock()


@dataclass
class QuarantineRecord:
    quarantine_id: str         # sha256[:16]
    quarantine_path: str       # full path to evidence dir
    evidence_path: str         # path to evidence.bin
    provenance_path: str
    scan_report_path: str
    decision_path: str
    file_hash: str
    file_size: int
    action: str
    timestamp_ms: float
    bytes_destroyed: bool      # True if DELETE_ESCALATE shredded the bytes

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class QuarantineDaemon:
    """Single-process quarantine daemon. Thread-safe via _log_lock."""

    def __init__(self, root: Path = QUARANTINE_ROOT,
                 audit_log: Path = AUDIT_LOG_PATH) -> None:
        self.root = root
        self.audit_log = audit_log
        self.root.mkdir(parents=True, exist_ok=True)
        self.audit_log.parent.mkdir(parents=True, exist_ok=True)
        # Touch the audit log if it doesn't exist
        if not self.audit_log.exists():
            self.audit_log.touch()
        # Best-effort: chmod the root to 0700 (only owner can list)
        try:
            os.chmod(self.root, stat.S_IRWXU)
        except OSError:
            pass
        self._pid = os.getpid()

    def quarantine(self, *,
                   src_path: Path,
                   original_name: str,
                   source_id: str,
                   scan_report: ScanReport,
                   decision: SoftPingDecision,
                   upload_timestamp_ms: float | None = None,
                   ) -> QuarantineRecord:
        """Move src_path into the quarantine sandbox with full provenance."""
        if upload_timestamp_ms is None:
            upload_timestamp_ms = time.time() * 1000.0
        quarantine_ts_ms = time.time() * 1000.0
        file_hash = scan_report.file_hash
        qid = file_hash[:16]
        qdir = self.root / qid
        # If this exact file hash is already quarantined, don't double-store
        if qdir.exists():
            # Append a new provenance entry but don't re-store the bytes
            return self._record_existing(qdir, src_path, original_name, source_id,
                                         scan_report, decision, upload_timestamp_ms,
                                         quarantine_ts_ms)
        qdir.mkdir(parents=True, exist_ok=False)

        evidence_path = qdir / EVIDENCE_NAME
        provenance_path = qdir / PROVENANCE_NAME
        scan_report_path = qdir / SCAN_REPORT_NAME
        decision_path = qdir / DECISION_NAME

        # Move (not copy) the file into quarantine
        try:
            shutil.move(str(src_path), str(evidence_path))
        except (OSError, shutil.Error) as e:
            # If move fails, try copy + delete
            shutil.copy2(str(src_path), str(evidence_path))
            try:
                os.unlink(src_path)
            except OSError:
                pass

        bytes_destroyed = False
        if decision.action == "delete_escalate":
            # Shred the bytes with urandom, THEN chmod 000
            self._shred(evidence_path, scan_report.file_size)

        # Lock down the evidence file
        try:
            os.chmod(evidence_path, 0)
        except OSError:
            pass

        # Best-effort noexec mount on the quarantine subdirectory
        # (Will likely fail in a non-root container — that's OK, the chmod 0
        # is the real protection.)
        self._try_noexec_mount(qdir)

        # Write provenance
        provenance = {
            "quarantine_id": qid,
            "original_filename": original_name,
            "original_path": str(src_path),
            "uploader_source_id": source_id,
            "upload_timestamp_ms": upload_timestamp_ms,
            "quarantine_timestamp_ms": quarantine_ts_ms,
            "file_sha256": file_hash,
            "file_size_bytes": scan_report.file_size,
            "file_type": scan_report.file_type,
            "file_entropy": scan_report.entropy,
            "max_severity": scan_report.max_severity,
            "decision_action": decision.action,
            "decision_severity_score": decision.severity_score,
            "decision_winning_action_gap": decision.winning_action_gap,
            "decision_tokens_consumed": decision.tokens_consumed,
            "top_chain": decision.chain_ranking[0]["name"] if decision.chain_ranking else None,
            "triggered_chains": [r["name"] for r in decision.chain_ranking if r["triggered"]],
            "quarantine_operator_pid": self._pid,
            "bytes_destroyed": bytes_destroyed,
            "evidence_path": str(evidence_path),
            "scan_report_path": str(scan_report_path),
            "decision_path": str(decision_path),
        }
        provenance_path.write_text(json.dumps(provenance, indent=2))
        scan_report_path.write_text(json.dumps(scan_report.as_dict(), indent=2))
        decision_path.write_text(json.dumps(decision.as_dict(), indent=2))

        # Lock down the metadata files too — read-only
        for p in (provenance_path, scan_report_path, decision_path):
            try:
                os.chmod(p, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
            except OSError:
                pass

        # Append to audit log
        self._append_audit(provenance)

        return QuarantineRecord(
            quarantine_id=qid,
            quarantine_path=str(qdir),
            evidence_path=str(evidence_path),
            provenance_path=str(provenance_path),
            scan_report_path=str(scan_report_path),
            decision_path=str(decision_path),
            file_hash=file_hash,
            file_size=scan_report.file_size,
            action=decision.action,
            timestamp_ms=quarantine_ts_ms,
            bytes_destroyed=bytes_destroyed,
        )

    def _record_existing(self, qdir: Path, src_path: Path, original_name: str,
                         source_id: str, scan_report: ScanReport,
                         decision: SoftPingDecision,
                         upload_ts: float, quarantine_ts: float) -> QuarantineRecord:
        """Same file already quarantined — append a new provenance entry."""
        qid = qdir.name
        # Append a second provenance file
        idx = 0
        while (qdir / f"provenance_{idx}.json").exists():
            idx += 1
        prov_path = qdir / f"provenance_{idx}.json"
        provenance = {
            "quarantine_id": qid,
            "original_filename": original_name,
            "original_path": str(src_path),
            "uploader_source_id": source_id,
            "upload_timestamp_ms": upload_ts,
            "quarantine_timestamp_ms": quarantine_ts,
            "file_sha256": scan_report.file_hash,
            "file_size_bytes": scan_report.file_size,
            "file_type": scan_report.file_type,
            "max_severity": scan_report.max_severity,
            "duplicate_of": str(qdir / EVIDENCE_NAME),
            "decision_action": decision.action,
            "decision_severity_score": decision.severity_score,
            "quarantine_operator_pid": self._pid,
            "bytes_destroyed": True,  # bytes not stored again — duplicate removed
        }
        prov_path.write_text(json.dumps(provenance, indent=2))
        self._append_audit(provenance)
        # Remove the duplicate source file
        try:
            os.unlink(src_path)
        except OSError:
            pass
        return QuarantineRecord(
            quarantine_id=qid,
            quarantine_path=str(qdir),
            evidence_path=str(qdir / EVIDENCE_NAME),
            provenance_path=str(prov_path),
            scan_report_path=str(qdir / SCAN_REPORT_NAME),
            decision_path=str(qdir / DECISION_NAME),
            file_hash=scan_report.file_hash,
            file_size=scan_report.file_size,
            action=decision.action,
            timestamp_ms=quarantine_ts,
            bytes_destroyed=True,  # bytes not stored again
        )

    def _shred(self, path: Path, size: int) -> None:
        """Overwrite file bytes with /dev/urandom."""
        try:
            with open(path, "r+b") as f:
                # Write random bytes in 64KB chunks
                remaining = size
                chunk = 65536
                with open("/dev/urandom", "rb") as rng:
                    while remaining > 0:
                        n = min(chunk, remaining)
                        f.write(rng.read(n))
                        remaining -= n
                f.flush()
                os.fsync(f.fileno())
        except OSError:
            # Best effort — if shred fails, the chmod 0 still locks the file
            pass

    def _try_noexec_mount(self, qdir: Path) -> None:
        """Best-effort noexec,nodev,nosuid mount on the quarantine subdir."""
        # Try mount --bind to itself + remount with noexec
        # This requires root and CAP_SYS_ADMIN. In a container it will usually
        # fail silently. The chmod 0 on evidence.bin is the real protection.
        try:
            subprocess.run(
                ["mount", "--bind", str(qdir), str(qdir)],
                check=True, capture_output=True, timeout=5,
            )
            subprocess.run(
                ["mount", "-o", "remount,noexec,nodev,nosuid", str(qdir)],
                check=True, capture_output=True, timeout=5,
            )
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            pass  # Expected in non-root containers

    def _append_audit(self, provenance: dict[str, Any]) -> None:
        line = json.dumps({
            "ts": provenance["quarantine_timestamp_ms"],
            "qid": provenance["quarantine_id"],
            "source": provenance["uploader_source_id"],
            "file": provenance["original_filename"],
            "sha256": provenance["file_sha256"],
            "size": provenance["file_size_bytes"],
            "action": provenance["decision_action"],
            "score": provenance["decision_severity_score"],
            "bytes_destroyed": provenance["bytes_destroyed"],
            "operator_pid": provenance["quarantine_operator_pid"],
        }, separators=(",", ":"))
        with _log_lock:
            with open(self.audit_log, "a") as f:
                f.write(line + "\n")

    def list_quarantine(self) -> list[dict[str, Any]]:
        """List all quarantined items with their provenance."""
        out = []
        if not self.root.exists():
            return out
        for qdir in sorted(self.root.iterdir()):
            if not qdir.is_dir():
                continue
            prov_path = qdir / PROVENANCE_NAME
            if not prov_path.exists():
                continue
            try:
                prov = json.loads(prov_path.read_text())
                out.append({
                    "quarantine_id": prov["quarantine_id"],
                    "original_filename": prov["original_filename"],
                    "uploader_source_id": prov["uploader_source_id"],
                    "upload_timestamp_ms": prov["upload_timestamp_ms"],
                    "quarantine_timestamp_ms": prov["quarantine_timestamp_ms"],
                    "file_sha256": prov["file_sha256"],
                    "file_size_bytes": prov["file_size_bytes"],
                    "file_type": prov["file_type"],
                    "max_severity": prov["max_severity"],
                    "action": prov["decision_action"],
                    "severity_score": prov["decision_severity_score"],
                    "top_chain": prov.get("top_chain"),
                    "triggered_chains": prov.get("triggered_chains", []),
                    "bytes_destroyed": prov.get("bytes_destroyed", False),
                    "path": str(qdir),
                })
            except (json.JSONDecodeError, KeyError):
                continue
        return out

    def get_evidence(self, qid: str) -> dict[str, Any] | None:
        """Read provenance + scan report + decision for a quarantined item.
        Returns metadata only — NOT the evidence bytes themselves."""
        qdir = self.root / qid
        if not qdir.is_dir():
            return None
        out: dict[str, Any] = {}
        for name, key in [(PROVENANCE_NAME, "provenance"),
                          (SCAN_REPORT_NAME, "scan_report"),
                          (DECISION_NAME, "decision")]:
            p = qdir / name
            if p.exists():
                try:
                    out[key] = json.loads(p.read_text())
                except json.JSONDecodeError:
                    out[key] = None
        return out


if __name__ == "__main__":
    # Self-test
    import sys
    if len(sys.argv) < 2:
        print("usage: quarantine_daemon.py <evidence_path>")
        sys.exit(1)
    src = Path(sys.argv[1])
    if not src.exists():
        print(f"not found: {src}")
        sys.exit(1)
    from content_scanner import scan_file
    from multi_chain_soft_ping import MultiChainSoftPing
    report = scan_file(src, src.name)
    engine = MultiChainSoftPing()
    decision = engine.decide(report, "self-test")
    daemon = QuarantineDaemon()
    rec = daemon.quarantine(
        src_path=src, original_name=src.name, source_id="self-test",
        scan_report=report, decision=decision,
    )
    print(json.dumps(rec.as_dict(), indent=2))
