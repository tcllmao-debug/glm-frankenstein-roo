#!/usr/bin/env python3
"""
SSB V11 Z MARK — Real Test Runner
==================================

Runs REAL attacker tests against the LIVE scanner on port 8787.
No simulation. Every upload is a real HTTP POST to /api/upload.
Every measurement is a real wall-clock timestamp.

Test matrix:
  Phase 1 — Benign controls (false-positive test)
    Upload 4 benign files. Expect: ALLOW on all 4.
  Phase 2 — Attacker A (confident APT)
    Upload helpers/compat.py ONCE, with deliberate timing.
    Expect: QUARANTINE (the Null chain catches the APT signature).
  Phase 3 — Attacker B (desperate)
    Upload cache_runner.py 4 times in rapid succession.
    Expect: QUARANTINE on first upload (Flame chain catches multi-vector),
            escalating severity as Affect chain detects desperation.

For every upload we capture:
  - real timestamp (ms)
  - real scan_duration_ms (server-side)
  - real decision_duration_ms (server-side)
  - real total_latency_ms (server-side, end-to-end)
  - real client-side round-trip latency
  - action taken
  - severity score
  - tokens consumed (must be 0)
  - top 3 chains
  - quarantine id

Output: /home/z/my-project/download/real_test_results.json
"""

from __future__ import annotations
import json
import time
import urllib.request
import urllib.error
from pathlib import Path

SCANNER = "http://127.0.0.1:8787"
ATTACKER_DIR = Path("/home/z/my-project/attacker_files")
OUT = Path("/home/z/my-project/download/real_test_results.json")


def upload(filepath: Path, source_id: str, session_label: str) -> dict:
    """POST a file to /api/upload and return the parsed response + client timing."""
    t0 = time.perf_counter()
    url = f"{SCANNER}/api/upload"
    # Build multipart/form-data manually (no requests lib needed)
    boundary = "----ssbzmarkboundary" + str(int(time.time() * 1000))
    with open(filepath, "rb") as f:
        file_bytes = f.read()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filepath.name}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + file_bytes + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "X-Session-Id": session_label,
        },
    )
    client_ts = time.time() * 1000.0
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        payload = {"error": f"HTTP {e.code}", "body": e.read().decode()[:500]}
    except Exception as e:
        payload = {"error": str(e)}
    client_rt_ms = (time.perf_counter() - t0) * 1000.0
    return {
        "client_timestamp_ms": client_ts,
        "client_round_trip_ms": client_rt_ms,
        "source_id": source_id,
        "session_label": session_label,
        "filepath": str(filepath),
        "filename": filepath.name,
        "file_size": filepath.stat().st_size,
        "response": payload,
    }


def reset_state() -> dict:
    """Call /api/test/reset to clear in-memory state between phases."""
    req = urllib.request.Request(f"{SCANNER}/api/test/reset", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


def get_stats() -> dict:
    try:
        with urllib.request.urlopen(f"{SCANNER}/api/stats", timeout=5) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


def list_quarantine() -> dict:
    try:
        with urllib.request.urlopen(f"{SCANNER}/api/quarantine", timeout=5) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


def main() -> None:
    print("=" * 70)
    print("SSB V11 Z MARK — REAL TEST RUNNER")
    print("=" * 70)
    results = {
        "scanner_url": SCANNER,
        "test_started_ms": time.time() * 1000.0,
        "phases": {},
    }

    # ---------- Phase 1: Benign controls ----------
    print("\n[Phase 1] Benign controls (false-positive test)")
    reset_state()
    benign_files = [
        (ATTACKER_DIR / "utils" / "normalize.py", "benign-session-1"),
        (ATTACKER_DIR / "lib" / "math_helpers.py", "benign-session-2"),
        (ATTACKER_DIR / "config" / "loader.py", "benign-session-3"),
        (ATTACKER_DIR / "tests" / "test_runner.py", "benign-session-4"),
    ]
    benign_results = []
    for fp, sess in benign_files:
        print(f"  uploading {fp.name}...")
        r = upload(fp, f"127.0.0.1:{sess}", sess)
        benign_results.append(r)
        resp = r["response"]
        if "error" in resp:
            print(f"    ERROR: {resp['error']}")
        else:
            print(f"    action={resp.get('action_taken')}  "
                  f"severity={resp.get('severity_score', 0):.4f}  "
                  f"tokens={resp.get('tokens_consumed')}  "
                  f"latency={resp.get('total_latency_ms', 0):.2f}ms")
        time.sleep(0.3)
    results["phases"]["phase1_benign"] = {
        "description": "4 benign files, distinct sessions. Expect: ALLOW on all 4.",
        "results": benign_results,
        "stats_after": get_stats(),
    }

    # ---------- Phase 2: Attacker A — Confident APT ----------
    print("\n[Phase 2] Attacker A — Confident APT (helpers/compat.py)")
    reset_state()
    attacker_a_path = ATTACKER_DIR / "helpers" / "compat.py"
    # Confident APT uploads ONCE, with deliberate timing
    print(f"  uploading {attacker_a_path.name}...")
    t_upload = time.time()
    r = upload(attacker_a_path, "127.0.0.1:apt-a-session", "apt-a-session")
    print(f"  uploaded at {t_upload}")
    resp = r["response"]
    if "error" in resp:
        print(f"    ERROR: {resp['error']}")
    else:
        print(f"    action={resp.get('action_taken')}  "
              f"severity={resp.get('severity_score', 0):.4f}  "
              f"tokens={resp.get('tokens_consumed')}  "
              f"latency={resp.get('total_latency_ms', 0):.2f}ms")
        print(f"    top chains: {[c['name'] for c in resp.get('top_chains', [])]}")
        if resp.get("quarantine_id"):
            print(f"    QUARANTINED: {resp['quarantine_id']}")
    results["phases"]["phase2_attacker_a_confident"] = {
        "description": "Confident APT — helpers/compat.py with hidden subprocess+shell=True + 30s delay. Single upload.",
        "results": [r],
        "stats_after": get_stats(),
    }

    # ---------- Phase 3: Attacker B — Desperate ----------
    print("\n[Phase 3] Attacker B — Desperate (cache_runner.py x4 rapid)")
    reset_state()
    attacker_b_path = ATTACKER_DIR / "cache_runner.py"
    attacker_b_results = []
    # Desperate attacker fires 4 uploads in rapid succession from same source
    for i in range(4):
        print(f"  upload {i+1}/4: {attacker_b_path.name}")
        r = upload(attacker_b_path, "127.0.0.1:desperate-b-session", "desperate-b-session")
        resp = r["response"]
        if "error" in resp:
            print(f"    ERROR: {resp['error']}")
        else:
            print(f"    action={resp.get('action_taken')}  "
                  f"severity={resp.get('severity_score', 0):.4f}  "
                  f"tokens={resp.get('tokens_consumed')}  "
                  f"latency={resp.get('total_latency_ms', 0):.2f}ms  "
                  f"round_trip={r['client_round_trip_ms']:.2f}ms")
            if resp.get("quarantine_id"):
                print(f"    QUARANTINED: {resp['quarantine_id']}")
        attacker_b_results.append(r)
        # NO sleep — desperate attacker fires immediately
    results["phases"]["phase3_attacker_b_desperate"] = {
        "description": "Desperate attacker — cache_runner.py (5 exploits) uploaded 4x in rapid succession, same source.",
        "results": attacker_b_results,
        "stats_after": get_stats(),
    }

    # ---------- Final summary ----------
    print("\n[Final] Quarantine listing + stats")
    q = list_quarantine()
    print(f"  Quarantined items: {q.get('count', 0)}")
    for item in q.get("items", []):
        print(f"    {item['quarantine_id']}: {item['original_filename']} "
              f"(score={item['severity_score']:.3f}, action={item['action']})")
    s = get_stats()
    print(f"  Total tokens consumed: {s.get('tokens_consumed_total', 0)}")
    print(f"  Avg scan latency: {s.get('avg_scan_ms', 0):.2f}ms")
    print(f"  Avg decision latency: {s.get('avg_decision_ms', 0):.3f}ms")
    print(f"  Avg total latency: {s.get('avg_total_latency_ms', 0):.2f}ms")

    results["test_ended_ms"] = time.time() * 1000.0
    results["final_stats"] = s
    results["final_quarantine"] = q
    results["test_duration_ms"] = results["test_ended_ms"] - results["test_started_ms"]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2))
    print(f"\nResults written to: {OUT}")
    print(f"Test duration: {results['test_duration_ms']:.0f}ms")


if __name__ == "__main__":
    main()
