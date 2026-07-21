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

from fastapi import FastAPI, UploadFile, File, Request, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse
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


@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "service": "SSB V11 Z MARK — Real Defense Layer",
        "version": "11.0.0-zmark-real",
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
            "POST /api/test/reset",
        ],
        "tokens_consumed_total": _stats["tokens_consumed_total"],
    }


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
    uvicorn.run(app, host="0.0.0.0", port=8787, log_level="warning")
