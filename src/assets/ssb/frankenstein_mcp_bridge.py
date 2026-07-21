#!/usr/bin/env python3
"""GLM Frankenstein — MCP Bridge. Exposes SSB tools over WebSocket+stdio."""
from __future__ import annotations
import os, sys, json, asyncio, importlib.util, pathlib, subprocess, socket
from datetime import datetime

SSB_DIR = pathlib.Path(os.environ.get("SSB_DIR", "."))
PORT = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8791

_TR = None
def triple_review_mod():
    global _TR
    if _TR is None:
        path = SSB_DIR / "patches" / "secret_review_v2_openclaw_hermes.py"
        if path.exists():
            spec = importlib.util.spec_from_file_location("ssb_tr", str(path))
            mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); _TR = mod
    return _TR

def scan_file(path):
    mod = triple_review_mod()
    if mod is None: return {"ok": False, "error": "triple-review patch not available"}
    try:
        content = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
        candidates = mod.findSecrets(content, path)
        results = [mod.tripleReview(c) for c in candidates]
        return {"ok": True, "result": {"count": len(results), "candidates": [r.__dict__ if hasattr(r, "__dict__") else r for r in results]}}
    except Exception as e: return {"ok": False, "error": str(e)}

def scan_directory(path, max_files=1000):
    mod = triple_review_mod()
    if mod is None: return {"ok": False, "error": "triple-review patch not available"}
    try:
        results = mod.scanPath(path, max_files=max_files)
        return {"ok": True, "result": {"count": len(results), "candidates": [r.__dict__ if hasattr(r, "__dict__") else r for r in results]}}
    except Exception as e: return {"ok": False, "error": str(e)}

def list_endpoints():
    return {"ok": True, "result": {
        "scanner": "http://127.0.0.1:8787", "persist": "http://127.0.0.1:8788",
        "activate": "http://127.0.0.1:8789", "portal": "http://127.0.0.1:3000",
        "triple": "http://127.0.0.1:8790/triple-review", "mcp": "ws://127.0.0.1:8791",
        "cli": "http://127.0.0.1:8792"}}

def run_patch(patch_name, args=None):
    path = SSB_DIR / "patches" / f"{patch_name}.py"
    if not path.exists(): return {"ok": False, "error": f"patch {patch_name} not found"}
    try:
        r = subprocess.run([sys.executable, str(path), *(args or [])], cwd=str(SSB_DIR), capture_output=True, text=True, timeout=120)
        return {"ok": r.returncode == 0, "result": r.stdout[-5000:], "stderr": r.stderr[-2000:]}
    except Exception as e: return {"ok": False, "error": str(e)}

def run_script(script_name, args=None):
    path = SSB_DIR / "scripts" / f"{script_name}.py"
    if not path.exists(): return {"ok": False, "error": f"script {script_name} not found"}
    try:
        r = subprocess.run([sys.executable, str(path), *(args or [])], cwd=str(SSB_DIR), capture_output=True, text=True, timeout=120)
        return {"ok": r.returncode == 0, "result": r.stdout[-5000:], "stderr": r.stderr[-2000:]}
    except Exception as e: return {"ok": False, "error": str(e)}

def stop_daemon(name):
    pid_file = SSB_DIR / "pids" / f"{name}.pid"
    if not pid_file.exists(): return {"ok": False, "error": f"no pid for {name}"}
    try:
        pid = int(pid_file.read_text().strip()); os.kill(pid, 15); pid_file.unlink()
        return {"ok": True, "result": f"stopped {name} pid={pid}"}
    except Exception as e: return {"ok": False, "error": str(e)}

def daemon_status():
    ports = {"scanner": 8787, "persist": 8788, "activate": 8789, "portal": 3000, "triple": 8790, "mcp": 8791, "cli": 8792}
    services = {}
    for name, port in ports.items():
        pid_file = SSB_DIR / "pids" / f"{name}.pid"
        pid = int(pid_file.read_text().strip()) if pid_file.exists() else None
        s = socket.socket(); s.settimeout(0.5)
        try: listening = s.connect_ex(("127.0.0.1", port)) == 0
        except: listening = False
        finally: s.close()
        services[name] = {"port": port, "pid": pid, "listening": listening}
    return {"ok": True, "result": services}

TOOLS = {
    "ssb.scan_file":       lambda a: scan_file(a["path"]),
    "ssb.scan_directory":  lambda a: scan_directory(a["path"], a.get("max_files", 1000)),
    "ssb.list_endpoints":  lambda a: list_endpoints(),
    "ssb.run_patch":       lambda a: run_patch(a["patch_name"], a.get("args", [])),
    "ssb.stop_daemon":     lambda a: stop_daemon(a["name"]),
    "ssb.daemon_status":   lambda a: daemon_status(),
    "ssb.run_script":      lambda a: run_script(a["script_name"], a.get("args", [])),
    "ssb.globe_fork":      lambda a: run_script("globe_forker", [a.get("target", "")]),
    "ssb.virtual_monitor": lambda a: run_script("virtual_monitor", [a.get("target", "")]),
    "ssb.persistent_ping": lambda a: run_script("persistent_connection", [a.get("target", "")]),
    "ssb.beast_scan":      lambda a: run_script("start_scanner_daemon", ["--beast", a.get("target", "")]),
    "ssb.quarantine":      lambda a: run_script("quarantine_daemon", [a.get("path", "")]),
    "ssb.raw_full":        lambda a: {"ok": True, "result": pathlib.Path(a["path"]).read_text(encoding="utf-8", errors="replace")[:50000]},
    "ssb.god_eye":         lambda a: run_script("scanner_server", ["--god-eye", a.get("target", "")]),
    "ssb.activate_to":     lambda a: run_script("activate_to_4000", [str(a.get("count", 4000))]),
}

async def handle_request(ws):
    async for message in ws:
        try: req = json.loads(message)
        except: await ws.send(json.dumps({"ok": False, "error": "invalid JSON"})); continue
        rid = req.get("id"); tool = req.get("tool"); args = req.get("args", {}) or {}
        if tool == "tools/list":
            await ws.send(json.dumps({"id": rid, "ok": True, "result": list(TOOLS.keys())})); continue
        if tool not in TOOLS:
            await ws.send(json.dumps({"id": rid, "ok": False, "error": f"unknown tool {tool}"})); continue
        try: await ws.send(json.dumps({"id": rid, **TOOLS[tool](args)}))
        except Exception as e: await ws.send(json.dumps({"id": rid, "ok": False, "error": str(e)}))

async def main_async():
    print(f"[{datetime.utcnow().isoformat()}Z] GLM Frankenstein MCP Bridge listening on ws://127.0.0.1:{PORT}", flush=True)
    try:
        import websockets
        async with websockets.serve(handle_request, "127.0.0.1", PORT):
            await asyncio.Future()
    except ImportError:
        print(f"[{datetime.utcnow().isoformat()}Z] websockets not installed — stdio bridge", flush=True)
        for line in sys.stdin:
            try:
                req = json.loads(line); rid = req.get("id"); tool = req.get("tool"); args = req.get("args", {}) or {}
                if tool == "tools/list": print(json.dumps({"id": rid, "ok": True, "result": list(TOOLS.keys())}), flush=True); continue
                if tool not in TOOLS: print(json.dumps({"id": rid, "ok": False, "error": f"unknown tool {tool}"}), flush=True); continue
                print(json.dumps({"id": rid, **TOOLS[tool](args)}), flush=True)
            except Exception as e: print(json.dumps({"ok": False, "error": str(e)}), flush=True)

if __name__ == "__main__":
    try: asyncio.run(main_async())
    except KeyboardInterrupt: pass
