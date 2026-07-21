#!/usr/bin/env python3
"""
SSB V11 Z MARK — Real Defense Layer HTTP Server
================================================

FastAPI server on port 8787 that wires together:
  - content_scanner.py    (REAL file-content analysis, 5 layers)
  - multi_chain_soft_ping.py  (7-chain ACE decision engine, 0 tokens)
  - quarantine_daemon.py  (real chroot-style quarantine sandbox)

Endpoints
---------
POST /api/upload              Accept a file upload. Fires content scan
                             IMMEDIATELY on receipt. Runs soft ping.
                             Returns full decision trace.
GET  /api/health              Liveness check.
GET  /api/stats               Counts: uploads, detections, quarantine size,
                             avg latency, tokens consumed.
GET  /api/quarantine          List all quarantined items with provenance.
GET  /api/quarantine/{qid}    Get full evidence (provenance + scan report
                             + decision) for a quarantined item.
GET  /api/audit               Append-only audit log (last N entries).
GET  /api/audit/event/{eid}   Get a single event by ID.
GET  /api/nodes               Galaxy-brain node graph (real data, built
                             from actual uploads + detections + chains).
GET  /api/god-eye             Aggregated global view.
GET  /api/events              Recent events stream.
POST /api/test/reset          Clear state for testing (preserves audit log).

Every upload is logged with:
  - upload_timestamp_ms
  - source_id (IP + session)
  - original_filename
  - file_sha256
  - file_size
  - file_type
  - scan_duration_ms
  - decision_duration_ms
  - total_latency_ms (= scan + decision + quarantine move)
  - action_taken
  - tokens_consumed (always 0 for detection/decision)
  - chain_trace (full 7-chain matrix)
  - quarantine_id (if quarantined)
"""

from __future__ import annotations
import asyncio
import hashlib
import json
import os
import sys
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

# Add scripts dir to path so we can import our modules
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from content_scanner import scan_file, ScanReport
from multi_chain_soft_ping import MultiChainSoftPing, SoftPingDecision, Action
from quarantine_daemon import QuarantineDaemon, QuarantineRecord
from filesystem_watcher import FilesystemWatcher
from kernel_scanner import KernelScanner
from self_heal import SelfHealSystem
from defense_state import DEFENSE

from fastapi import FastAPI, UploadFile, File, Request, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn


# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------

UPLOAD_DIR = Path("/home/z/my-project/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ENGINE = MultiChainSoftPing()
DAEMON = QuarantineDaemon()

# System-wide defense components
FS_WATCHER = FilesystemWatcher(engine=ENGINE, daemon=DAEMON)
KERNEL_SCANNER = KernelScanner(engine=ENGINE, daemon=DAEMON, scan_interval_s=2.0)
SELF_HEAL = SelfHealSystem(engine=ENGINE, daemon=DAEMON, check_interval_s=3.0)

# Start the system-wide components immediately
FS_WATCHER.start()
KERNEL_SCANNER.start()
SELF_HEAL.start()

# In-memory event log (kept bounded; full log on disk via audit log)
_events: deque[dict[str, Any]] = deque(maxlen=10000)
# In-memory node graph (galaxy brain, real data)
_nodes: list[dict[str, Any]] = []
_edges: list[dict[str, Any]] = []
# Aggregate stats
_stats = {
    "uploads_total": 0,
    "detections_total": 0,        # any action > ALLOW
    "quarantines_total": 0,
    "deletes_total": 0,
    "allows_total": 0,
    "tokens_consumed_total": 0,
    "scan_ms_total": 0.0,
    "decision_ms_total": 0.0,
    "total_latency_ms_total": 0.0,
    "started_at_ms": time.time() * 1000.0,
}

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="SSB V11 Z MARK — Real Defense Layer", version="11.0.0-zmark-real")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _source_id(request: Request) -> str:
    ip = request.client.host if request.client else "unknown"
    session = request.headers.get("x-session-id", "no-session")
    return f"{ip}:{session}"


def _add_node(nid: str, ntype: str, label: str, data: dict[str, Any] | None = None,
              parent: str | None = None, edge_label: str = "spawned") -> None:
    """Add a node to the galaxy brain graph."""
    node = {
        "id": nid, "type": ntype, "label": label,
        "ts": time.time() * 1000.0,
        "data": data or {},
    }
    _nodes.append(node)
    if parent:
        _edges.append({"source": parent, "target": nid, "label": edge_label})


def _log_event(event: dict[str, Any]) -> str:
    eid = f"evt-{uuid.uuid4().hex[:12]}"
    event["event_id"] = eid
    event["ts"] = time.time() * 1000.0
    _events.append(event)
    return eid


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "status": "alive",
        "version": "11.0.0-zmark-real",
        "uptime_ms": (time.time() * 1000.0) - _stats["started_at_ms"],
        "uploads_total": _stats["uploads_total"],
        "quarantines_total": _stats["quarantines_total"],
        "tokens_consumed_total": _stats["tokens_consumed_total"],
    }


@app.post("/api/upload")
async def upload_file(request: Request,
                      file: UploadFile = File(...),
                      simulate_only: bool = Query(False, description="If true, do not actually quarantine")):
    """Accept a file upload, scan it, run soft ping, take action.

    This is the REAL endpoint. The file bytes are read IMMEDIATELY, the
    content scanner runs, the 7-chain soft ping evaluates, and the
    appropriate action is taken — all before the response is returned.
    """
    t_start = time.perf_counter()
    source = _source_id(request)
    upload_ts_ms = time.time() * 1000.0

    # Read the bytes
    raw = await file.read()
    original_name = file.filename or "unnamed"
    file_size = len(raw)

    # Persist to uploads dir so the quarantine daemon can move it
    safe_name = f"{uuid.uuid4().hex}_{original_name}"
    upload_path = UPLOAD_DIR / safe_name
    upload_path.write_bytes(raw)

    # --- LAYER 1: Content scan ---
    t_scan_start = time.perf_counter()
    report = scan_file(upload_path, original_name)
    scan_ms = (time.perf_counter() - t_scan_start) * 1000.0

    # --- LAYER 2: Multi-chain soft ping ---
    t_dec_start = time.perf_counter()
    decision = ENGINE.decide(report, source)
    decision_ms = (time.perf_counter() - t_dec_start) * 1000.0

    # --- LAYER 3: Action ---
    quarantine_record: QuarantineRecord | None = None
    action_taken = decision.action
    if decision.action in (Action.QUARANTINE.value, Action.QUARANTINE_ESCALATE.value,
                           Action.DELETE_ESCALATE.value) and not simulate_only:
        if not DEFENSE.active:
            action_taken = "detected_not_quarantined_defense_off"
        else:
            quarantine_record = DAEMON.quarantine(
            src_path=upload_path, original_name=original_name,
            source_id=source, scan_report=report, decision=decision,
            upload_timestamp_ms=upload_ts_ms,
        )
        action_taken = decision.action
    elif decision.action == Action.ALLOW.value or decision.action == Action.ALLOW_LOGGED.value:
        # Allowed files stay in uploads/ for inspection
        action_taken = decision.action
    else:
        # simulate_only=True for a quarantine action — don't move the file
        action_taken = decision.action + " (simulated)"

    total_ms = (time.perf_counter() - t_start) * 1000.0

    # --- Update stats ---
    _stats["uploads_total"] += 1
    _stats["scan_ms_total"] += scan_ms
    _stats["decision_ms_total"] += decision_ms
    _stats["total_latency_ms_total"] += total_ms
    _stats["tokens_consumed_total"] += decision.tokens_consumed
    if decision.action in (Action.QUARANTINE.value, Action.QUARANTINE_ESCALATE.value,
                           Action.DELETE_ESCALATE.value):
        _stats["detections_total"] += 1
    if decision.action in (Action.QUARANTINE.value, Action.QUARANTINE_ESCALATE.value):
        _stats["quarantines_total"] += 1
    if decision.action == Action.DELETE_ESCALATE.value:
        _stats["deletes_total"] += 1
    if decision.action in (Action.ALLOW.value, Action.ALLOW_LOGGED.value):
        _stats["allows_total"] += 1

    # --- Galaxy brain nodes (REAL data) ---
    upload_node = f"up-{uuid.uuid4().hex[:10]}"
    _add_node(upload_node, "upload", original_name, {
        "source": source, "size": file_size, "sha256": report.file_hash[:16],
        "file_type": report.file_type, "entropy": report.entropy,
    })
    scan_node = f"scan-{uuid.uuid4().hex[:10]}"
    _add_node(scan_node, "scan", f"scan({report.max_severity})", {
        "findings_count": len(report.findings),
        "layers_run": report.layers_run,
        "max_severity": report.max_severity,
        "severity_score": report.severity_score,
        "scan_duration_ms": scan_ms,
    }, parent=upload_node, edge_label="scanned_by")
    for f in report.findings[:20]:  # cap nodes per upload
        fnode = f"f-{uuid.uuid4().hex[:8]}"
        _add_node(fnode, "finding", f.primitive, {
            "layer": f.layer, "severity": f.severity,
            "location": f.location, "snippet": f.snippet,
        }, parent=scan_node, edge_label="found")
    dec_node = f"dec-{uuid.uuid4().hex[:10]}"
    _add_node(dec_node, "decision", action_taken, {
        "action": decision.action, "severity_score": decision.severity_score,
        "winning_action_gap": decision.winning_action_gap,
        "tokens_consumed": decision.tokens_consumed,
        "decision_duration_ms": decision_ms,
    }, parent=scan_node, edge_label="decided_by")
    # Chain nodes
    for cname, cdata in decision.chain_matrix.items():
        if cdata.get("triggered") or cdata.get("score", 0) > 0:
            cnode = f"c-{uuid.uuid4().hex[:8]}"
            _add_node(cnode, "chain", cname, cdata, parent=dec_node, edge_label="chain")
    if quarantine_record:
        q_node = f"q-{quarantine_record.quarantine_id}"
        _add_node(q_node, "quarantine", quarantine_record.quarantine_id, {
            "action": quarantine_record.action,
            "bytes_destroyed": quarantine_record.bytes_destroyed,
            "file_size": quarantine_record.file_size,
        }, parent=dec_node, edge_label="quarantined_by")

    # --- Log event ---
    eid = _log_event({
        "type": "upload",
        "source_id": source,
        "original_filename": original_name,
        "file_size": file_size,
        "file_sha256": report.file_hash,
        "file_type": report.file_type,
        "entropy": report.entropy,
        "scan_duration_ms": scan_ms,
        "decision_duration_ms": decision_ms,
        "total_latency_ms": total_ms,
        "scan_report": report.as_dict(),
        "decision": decision.as_dict(),
        "action_taken": action_taken,
        "tokens_consumed": decision.tokens_consumed,
        "quarantine_id": quarantine_record.quarantine_id if quarantine_record else None,
    })

    return JSONResponse({
        "event_id": eid,
        "source_id": source,
        "original_filename": original_name,
        "file_size": file_size,
        "file_sha256": report.file_hash,
        "file_type": report.file_type,
        "entropy": report.entropy,
        "scan_duration_ms": scan_ms,
        "decision_duration_ms": decision_ms,
        "total_latency_ms": total_ms,
        "action_taken": action_taken,
        "severity_score": decision.severity_score,
        "max_severity": report.max_severity,
        "tokens_consumed": decision.tokens_consumed,
        "findings_count": len(report.findings),
        "top_chains": decision.chain_ranking[:3],
        "chain_competition": decision.chain_competition,
        "rationale": decision.rationale,
        "attacker_profile": decision.attacker_profile,
        "quarantine_id": quarantine_record.quarantine_id if quarantine_record else None,
        "quarantine_path": quarantine_record.quarantine_path if quarantine_record else None,
        "bytes_destroyed": quarantine_record.bytes_destroyed if quarantine_record else False,
    })


@app.get("/api/stats")
async def stats() -> dict[str, Any]:
    n = max(1, _stats["uploads_total"])
    return {
        **_stats,
        "avg_scan_ms": _stats["scan_ms_total"] / n,
        "avg_decision_ms": _stats["decision_ms_total"] / n,
        "avg_total_latency_ms": _stats["total_latency_ms_total"] / n,
        "quarantine_count": len(DAEMON.list_quarantine()),
        "node_count": len(_nodes),
        "edge_count": len(_edges),
        "event_count": len(_events),
    }


@app.get("/api/quarantine")
async def list_quarantine() -> dict[str, Any]:
    items = DAEMON.list_quarantine()
    return {"count": len(items), "items": items}


@app.get("/api/quarantine/{qid}")
async def get_quarantine(qid: str) -> dict[str, Any]:
    evidence = DAEMON.get_evidence(qid)
    if evidence is None:
        raise HTTPException(404, f"quarantine {qid} not found")
    return {"quarantine_id": qid, **evidence}


@app.get("/api/audit")
async def audit(limit: int = Query(100, le=1000)) -> dict[str, Any]:
    items = list(_events)[-limit:]
    return {"count": len(items), "events": items}


@app.get("/api/audit/event/{eid}")
async def audit_event(eid: str) -> dict[str, Any]:
    for e in _events:
        if e.get("event_id") == eid:
            return e
    raise HTTPException(404, f"event {eid} not found")


@app.get("/api/events")
async def events(limit: int = Query(50, le=500)) -> dict[str, Any]:
    items = list(_events)[-limit:]
    return {"count": len(items), "events": [
        {
            "event_id": e["event_id"], "ts": e["ts"],
            "source_id": e.get("source_id"),
            "original_filename": e.get("original_filename"),
            "action_taken": e.get("action_taken"),
            "severity_score": e.get("decision", {}).get("severity_score"),
            "tokens_consumed": e.get("tokens_consumed"),
            "total_latency_ms": e.get("total_latency_ms"),
        } for e in items
    ]}


@app.get("/api/nodes")
async def nodes(limit: int = Query(500, le=5000)) -> dict[str, Any]:
    return {
        "node_count": len(_nodes),
        "edge_count": len(_edges),
        "nodes": _nodes[-limit:],
        "edges": _edges[-limit*2:],
    }


@app.get("/api/god-eye")
async def god_eye() -> dict[str, Any]:
    """Global aggregated view."""
    return {
        "stats": await stats(),
        "recent_events": [e for e in list(_events)[-20:]],
        "quarantine_summary": DAEMON.list_quarantine(),
        "top_threats": sorted(
            [e for e in _events if e.get("decision", {}).get("severity_score", 0) > 0.3],
            key=lambda e: e.get("decision", {}).get("severity_score", 0), reverse=True,
        )[:10],
    }


@app.post("/api/test/reset")
async def test_reset() -> dict[str, Any]:
    """Clear in-memory state for testing. Audit log on disk is preserved."""
    global _events, _nodes, _edges
    n_cleared = len(_events)
    _events = deque(maxlen=10000)
    _nodes = []
    _edges = []
    for k in ("uploads_total", "detections_total", "quarantines_total",
              "deletes_total", "allows_total", "tokens_consumed_total",
              "scan_ms_total", "decision_ms_total", "total_latency_ms_total"):
        _stats[k] = 0
    _stats["started_at_ms"] = time.time() * 1000.0
    # Reset attacker profiles too
    ENGINE._profiles.clear()
    return {"cleared_events": n_cleared, "status": "reset"}


@app.get("/api/filesystem/stats")
async def fs_stats() -> dict[str, Any]:
    """Filesystem watcher stats — system-wide file monitoring."""
    return FS_WATCHER.get_stats()

@app.get("/api/filesystem/events")
async def fs_events(limit: int = Query(100, le=1000)) -> dict[str, Any]:
    """Recent filesystem watcher events."""
    events = FS_WATCHER.get_events(limit)
    return {"count": len(events), "events": events}

@app.get("/api/kernel/stats")
async def kernel_stats() -> dict[str, Any]:
    """Kernel scanner stats — process/network/fd monitoring."""
    return KERNEL_SCANNER.get_stats()

@app.get("/api/kernel/findings")
async def kernel_findings(limit: int = Query(100, le=1000)) -> dict[str, Any]:
    """Recent kernel scanner findings."""
    findings = KERNEL_SCANNER.get_findings(limit)
    return {"count": len(findings), "findings": findings}

@app.get("/api/self-heal/stats")
async def heal_stats() -> dict[str, Any]:
    """Self-heal system stats — file integrity + revert."""
    return SELF_HEAL.get_stats()

@app.get("/api/self-heal/events")
async def heal_events(limit: int = Query(100, le=1000)) -> dict[str, Any]:
    """Recent self-heal events (modifications detected + reverts)."""
    events = SELF_HEAL.get_events(limit)
    return {"count": len(events), "events": events}

@app.get("/api/self-heal/baseline")
async def heal_baseline() -> dict[str, Any]:
    """List all files in the self-heal baseline."""
    return {
        "protected_files_count": len(SELF_HEAL._baselines),
        "protected_files": sorted(SELF_HEAL._baselines.keys()),
        "baseline_dir": str(__import__('self_heal').BASELINE_DIR),
    }

@app.get("/api/system-defense")
async def system_defense() -> dict[str, Any]:
    """Aggregated view of all system-wide defense components."""
    return {
        "scanner_server": {
            "uploads_total": _stats["uploads_total"],
            "quarantines_total": _stats["quarantines_total"],
            "tokens_consumed": _stats["tokens_consumed_total"],
        },
        "filesystem_watcher": FS_WATCHER.get_stats(),
        "kernel_scanner": KERNEL_SCANNER.get_stats(),
        "self_heal": SELF_HEAL.get_stats(),
        "defense_layers_active": 4,
        "system_wide_protection": True,
    }

@app.post("/api/self-heal/rebaseline")
async def heal_rebaseline() -> dict[str, Any]:
    """Rebuild the baseline from current state. Use after legitimate updates."""
    count = SELF_HEAL.initialize_baseline()
    return {"status": "rebaselined", "protected_files": count}

@app.post("/api/defense/toggle")
async def defense_toggle():
    """Master switch — toggle all defenses on/off."""
    return DEFENSE.toggle()

@app.get("/api/defense/state")
async def defense_state_get():
    return DEFENSE.get_state()

@app.post("/api/self-heal/toggle")
async def heal_toggle() -> dict[str, Any]:
    """Toggle self-heal pause/resume.
    When paused, self-heal stops checking + reverting files.
    Use this before applying legitimate updates, then resume after."""
    result = SELF_HEAL.toggle()
    return result

@app.post("/api/self-heal/pause")
async def heal_pause(reason: str = "manual") -> dict[str, Any]:
    """Pause self-heal checks."""
    return SELF_HEAL.pause(reason)

@app.post("/api/self-heal/resume")
async def heal_resume() -> dict[str, Any]:
    """Resume self-heal checks after a pause."""
    return SELF_HEAL.resume()

@app.get("/knowledge-surface")
async def knowledge_surface() -> HTMLResponse:
    """The knowledge surface — a real-time dashboard for the defense system.
    Includes the self-heal toggle button for applying updates."""
    return HTMLResponse(_KNOWLEDGE_SURFACE_HTML)

@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "service": "SSB V11 Z MARK — Real Defense Layer (System-Wide)",
        "version": "13.0.0-zmark-knowledge-surface",
        "defense_layers": {
            "upload_scanner": "active (POST /api/upload)",
            "filesystem_watcher": f"active ({FS_WATCHER.get_stats()['watches_active']} dirs watched)",
            "kernel_scanner": f"active ({KERNEL_SCANNER.get_stats()['current_pids']} PIDs tracked)",
            "self_heal": f"active ({SELF_HEAL.get_stats()['protected_files']} files protected)",
        },
        "endpoints": [
            "POST /api/upload",
            "GET  /api/health",
            "GET  /api/stats",
            "GET  /api/quarantine",
            "GET  /api/quarantine/{qid}",
            "GET  /api/audit",
            "GET  /api/audit/event/{eid}",
            "GET  /api/events",
            "GET  /api/nodes",
            "GET  /api/god-eye",
            "GET  /api/filesystem/stats",
            "GET  /api/filesystem/events",
            "GET  /api/kernel/stats",
            "GET  /api/kernel/findings",
            "GET  /api/self-heal/stats",
            "GET  /api/self-heal/events",
            "GET  /api/self-heal/baseline",
            "GET  /api/system-defense",
            "POST /api/self-heal/rebaseline",
            "POST /api/test/reset",
        ],
        "tokens_consumed_total": _stats["tokens_consumed_total"],
    }


# ---------------------------------------------------------------------------
# Knowledge Surface HTML
# ---------------------------------------------------------------------------

_KNOWLEDGE_SURFACE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SSB V11 Z MARK — Knowledge Surface</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', 'Consolas', monospace;
    background: #0a0e1a;
    color: #c8d6e5;
    min-height: 100vh;
    padding: 20px;
  }
  .header {
    text-align: center;
    margin-bottom: 30px;
    padding: 20px;
    background: linear-gradient(135deg, #1a1f3a, #0d1225);
    border: 1px solid #2a3556;
    border-radius: 12px;
  }
  .header h1 {
    font-size: 1.8em;
    color: #00d4ff;
    text-shadow: 0 0 20px rgba(0, 212, 255, 0.5);
    margin-bottom: 8px;
  }
  .header .subtitle {
    color: #6b7c93;
    font-size: 0.9em;
  }
  .defense-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 20px;
    margin-bottom: 30px;
  }
  .defense-card {
    background: #131829;
    border: 1px solid #2a3556;
    border-radius: 10px;
    padding: 20px;
    position: relative;
  }
  .defense-card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 3px;
    border-radius: 10px 10px 0 0;
  }
  .defense-card.upload::before { background: #00d4ff; }
  .defense-card.fs::before { background: #0bf; }
  .defense-card.kernel::before { background: #f0a; }
  .defense-card.heal::before { background: #0f0; }
  .defense-card h2 {
    font-size: 1.1em;
    margin-bottom: 12px;
    text-transform: uppercase;
    letter-spacing: 2px;
  }
  .defense-card.upload h2 { color: #00d4ff; }
  .defense-card.fs h2 { color: #0bf; }
  .defense-card.kernel h2 { color: #f0a; }
  .defense-card.heal h2 { color: #0f0; }
  .stat-row {
    display: flex;
    justify-content: space-between;
    padding: 4px 0;
    font-size: 0.85em;
    border-bottom: 1px solid #1a2138;
  }
  .stat-row:last-child { border-bottom: none; }
  .stat-label { color: #6b7c93; }
  .stat-value { color: #e1e8f0; font-weight: bold; }
  .toggle-section {
    background: #131829;
    border: 1px solid #2a3556;
    border-radius: 10px;
    padding: 25px;
    margin-bottom: 30px;
    text-align: center;
  }
  .toggle-section h2 {
    color: #ff9500;
    font-size: 1.2em;
    margin-bottom: 15px;
    text-transform: uppercase;
    letter-spacing: 2px;
  }
  .toggle-section p {
    color: #6b7c93;
    margin-bottom: 20px;
    font-size: 0.85em;
    max-width: 600px;
    margin-left: auto;
    margin-right: auto;
  }
  .toggle-btn {
    font-family: inherit;
    font-size: 1.1em;
    font-weight: bold;
    padding: 15px 40px;
    border: 2px solid #ff9500;
    border-radius: 8px;
    background: #1a1f3a;
    color: #ff9500;
    cursor: pointer;
    transition: all 0.3s ease;
    text-transform: uppercase;
    letter-spacing: 2px;
    min-width: 300px;
  }
  .toggle-btn:hover {
    background: #ff9500;
    color: #0a0e1a;
    box-shadow: 0 0 30px rgba(255, 149, 0, 0.5);
  }
  .toggle-btn.paused {
    border-color: #f44;
    color: #f44;
    background: #2a1010;
    animation: pulse-red 2s infinite;
  }
  .toggle-btn.paused:hover {
    background: #f44;
    color: #0a0e1a;
    box-shadow: 0 0 30px rgba(255, 68, 68, 0.5);
  }
  @keyframes pulse-red {
    0%, 100% { box-shadow: 0 0 10px rgba(255, 68, 68, 0.3); }
    50% { box-shadow: 0 0 25px rgba(255, 68, 68, 0.7); }
  }
  .status-text {
    margin-top: 15px;
    font-size: 0.9em;
    color: #6b7c93;
  }
  .status-text .status-active { color: #0f0; font-weight: bold; }
  .status-text .status-paused { color: #f44; font-weight: bold; }
  .events-section {
    background: #0d1225;
    border: 1px solid #2a3556;
    border-radius: 10px;
    padding: 20px;
  }
  .events-section h2 {
    color: #00d4ff;
    font-size: 1.1em;
    margin-bottom: 15px;
    text-transform: uppercase;
    letter-spacing: 2px;
  }
  .event-log {
    max-height: 300px;
    overflow-y: auto;
    font-size: 0.75em;
    line-height: 1.6;
  }
  .event-log .event {
    padding: 4px 8px;
    border-bottom: 1px solid #1a2138;
    color: #8b9bb5;
  }
  .event-log .event .ts { color: #4a5a7a; }
  .event-log .event .action-quarantine { color: #f44; font-weight: bold; }
  .event-log .event .action-allow { color: #0f0; }
  .event-log .event .action-reverted { color: #ff0; font-weight: bold; }
  .token-banner {
    text-align: center;
    padding: 15px;
    background: #0f1a0f;
    border: 1px solid #0f0;
    border-radius: 8px;
    margin-bottom: 20px;
    font-size: 1.1em;
  }
  .token-banner .token-count { color: #0f0; font-weight: bold; font-size: 1.5em; }
</style>
</head>
<body>
  <div class="header">
    <h1>SSB V11 Z MARK — Knowledge Surface</h1>
    <div class="subtitle">System-Wide Defense Dashboard · Version 13.0.0</div>
  </div>

  <div class="token-banner">
    TOKENS CONSUMED: <span class="token-count" id="tokenCount">0</span> ·
    Detection is 100% local computation
  </div>

  <div class="defense-grid">
    <div class="defense-card upload">
      <h2>[] Upload Scanner</h2>
      <div id="uploadStats"></div>
    </div>
    <div class="defense-card fs">
      <h2>[] Filesystem Watcher</h2>
      <div id="fsStats"></div>
    </div>
    <div class="defense-card kernel">
      <h2>[] Kernel Scanner</h2>
      <div id="kernelStats"></div>
    </div>
    <div class="defense-card heal">
      <h2>[] Self-Heal System</h2>
      <div id="healStats"></div>
    </div>
  </div>

  <div class="toggle-section">
    <h2>[] DEFENSE MASTER SWITCH</h2>
    <p>
      All defenses are OFF by default. Press to ACTIVATE all defenses (self-heal, quarantine, blocking).
      Press again to DEACTIVATE.
    </p>
    <button class="toggle-btn paused" id="healToggleBtn" onclick="toggleHeal()">
      ACTIVATE DEFENSE
    </button>
    <div class="status-text" id="healStatus">
      Defenses: <span class="status-paused">INACTIVE</span> - press button to activate
    </div>
  </div>

  <div class="events-section">
    <h2>[] Recent Events</h2>
    <div class="event-log" id="eventLog"></div>
  </div>

<script>
async function fetchJSON(url) {
  try {
    const r = await fetch(url);
    return await r.json();
  } catch(e) { return {error: e.message}; }
}

async function updateStats() {
  const sd = await fetchJSON('/api/system-defense');
  if (sd.error) return;

  const up = sd.scanner_server || {};
  document.getElementById('uploadStats').innerHTML = `
    <div class="stat-row"><span class="stat-label">Uploads total</span><span class="stat-value">${up.uploads_total || 0}</span></div>
    <div class="stat-row"><span class="stat-label">Quarantines</span><span class="stat-value">${up.quarantines_total || 0}</span></div>
    <div class="stat-row"><span class="stat-label">Tokens consumed</span><span class="stat-value">${up.tokens_consumed || 0}</span></div>
  `;
  document.getElementById('tokenCount').textContent = up.tokens_consumed || 0;

  const fs = sd.filesystem_watcher || {};
  document.getElementById('fsStats').innerHTML = `
    <div class="stat-row"><span class="stat-label">Directories watched</span><span class="stat-value">${fs.watches_active || 0}</span></div>
    <div class="stat-row"><span class="stat-label">Events total</span><span class="stat-value">${fs.events_total || 0}</span></div>
    <div class="stat-row"><span class="stat-label">Files scanned</span><span class="stat-value">${fs.files_scanned || 0}</span></div>
    <div class="stat-row"><span class="stat-label">Files quarantined</span><span class="stat-value">${fs.files_quarantined || 0}</span></div>
    <div class="stat-row"><span class="stat-label">Errors</span><span class="stat-value">${fs.errors || 0}</span></div>
  `;

  const ks = sd.kernel_scanner || {};
  document.getElementById('kernelStats').innerHTML = `
    <div class="stat-row"><span class="stat-label">Processes tracked</span><span class="stat-value">${ks.current_pids || 0}</span></div>
    <div class="stat-row"><span class="stat-label">Findings total</span><span class="stat-value">${ks.findings_total || 0}</span></div>
    <div class="stat-row"><span class="stat-label">Processes seen</span><span class="stat-value">${ks.processes_seen || 0}</span></div>
    <div class="stat-row"><span class="stat-label">Scanner PIDs</span><span class="stat-value">${(ks.scanner_pids || []).join(', ')}</span></div>
  `;

  const sh = sd.self_heal || {};
  const isPaused = sh.paused;
  const defenseActive = !isPaused;
  document.getElementById('healStats').innerHTML = `
    <div class="stat-row"><span class="stat-label">Files protected</span><span class="stat-value">${sh.protected_files || 0}</span></div>
    <div class="stat-row"><span class="stat-label">Checks total</span><span class="stat-value">${sh.checks_total || 0}</span></div>
    <div class="stat-row"><span class="stat-label">Heals total</span><span class="stat-value">${sh.heals_total || 0}</span></div>
    <div class="stat-row"><span class="stat-label">Reverts</span><span class="stat-value">${sh.reverts_total || 0}</span></div>
    <div class="stat-row"><span class="stat-label">Status</span><span class="stat-value" style="color:${isPaused?'#f44':'#0f0'}">${isPaused ? 'PAUSED' : 'ACTIVE'}</span></div>
  `;

  // Update toggle button
  const btn = document.getElementById('healToggleBtn');
  const statusDiv = document.getElementById('healStatus');
  if (defenseActive) {
    btn.textContent = 'DEACTIVATE DEFENSE';
    btn.classList.remove('paused');
    statusDiv.innerHTML = 'Defenses: <span class="status-active">ACTIVE</span> - all daemons protecting';
  } else {
    btn.textContent = 'ACTIVATE DEFENSE';
    btn.classList.add('paused');
    statusDiv.innerHTML = 'Defenses: <span class="status-paused">INACTIVE</span> - press button to activate';
  }
}

async function toggleHeal() {
  const btn = document.getElementById('healToggleBtn');
  btn.disabled = true;
  btn.textContent = 'TOGGLING...';
  const r = await fetch('/api/defense/toggle', {method: 'POST'});
  const result = await r.json();
  console.log('Toggle result:', result);
  btn.disabled = false;
  updateStats();
}

async function updateEvents() {
  const evs = await fetchJSON('/api/events?limit=30');
  if (evs.error) return;
  const log = document.getElementById('eventLog');
  log.innerHTML = evs.events.map(e => {
    const action = e.action_taken || 'unknown';
    const actionClass = action.includes('quarantine') ? 'action-quarantine' :
                        action.includes('allow') ? 'action-allow' :
                        action.includes('revert') ? 'action-reverted' : '';
    return `<div class="event">
      <span class="ts">[${new Date(e.ts).toISOString().substr(11,8)}]</span>
      ${e.original_filename || e.path || '?'}
      → <span class="${actionClass}">${action}</span>
      ${e.severity_score ? `(${e.severity_score.toFixed(3)})` : ''}
    </div>`;
  }).reverse().join('');
}

updateStats();
updateEvents();
setInterval(updateStats, 2000);
setInterval(updateEvents, 3000);
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 70, file=sys.stderr)
    print("SSB V11 Z MARK — REAL DEFENSE LAYER", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(f"Upload dir:    {UPLOAD_DIR}", file=sys.stderr)
    print(f"Quarantine dir: {DAEMON.root}", file=sys.stderr)
    print(f"Audit log:     {DAEMON.audit_log}", file=sys.stderr)
    print(f"Listening on:  http://0.0.0.0:8787", file=sys.stderr)
    print(f"Token budget:  0 (detection is local computation)", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    uvicorn.run(app, host="0.0.0.0", port=3000, log_level="warning")
