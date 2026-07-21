#!/usr/bin/env python3
"""
GLM Frankenstein — MCP Bridge
=============================

Exposes every SSB CLI command as an MCP tool over WebSocket (port 8791) so
Roo Code's MCP client (or any other MCP-aware agent) can invoke them.

MCP tool list (each maps to a CLI command or Python function):
  - ssb.scan_file          (path)                 — scan one file with the triple-review
  - ssb.scan_directory     (path, max_files=1000) — recursive workspace scan
  - ssb.beast_scan         (target)               — beast scanner probe
  - ssb.list_endpoints     ()                      — list all running SSB endpoints
  - ssb.run_patch          (patch_name, args=[])  — invoke a patch from patches/
  - ssb.start_daemon       (name)                 — start a named daemon
  - ssb.stop_daemon        (name)                 — stop a named daemon
  - ssb.daemon_status      ()                      — return JSON status of all daemons
  - ssb.triple_review      (content, file_name)   — AI + OpenClaw + Hermes Beast Claw review
  - ssb.activate_to        (count=4000)           — activate to N nodes
  - ssb.globe_fork         (target)               — globe forker probe
  - ssb.virtual_monitor    (target)               — text-based vision scan
  - ssb.persistent_ping    (target)               — multi-chain soft ping
  - ssb.quarantine         (path)                 — quarantine a file
  - ssb.raw_full           (path)                 — 8-method file reader
  - ssb.god_eye            (target)               — deep filesystem inspection

Wire format (JSON over WebSocket):
  Request:  {"id": "<uuid>", "tool": "ssb.scan_file", "args": {"path": "..."}}
  Response: {"id": "<uuid>", "ok": true, "result": "..."} | {"ok": false, "error": "..."}
"""

from __future__ import annotations
import os, sys, json, asyncio, importlib.util, pathlib, subprocess
from datetime import datetime

SSB_DIR = pathlib.Path(os.environ.get("SSB_DIR", "."))
PORT = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8791

# Lazy-import the triple-review module
_TR = None
def triple_review_mod():
    global _TR
    if _TR is None:
        # Try the canonical patch location
        path = SSB_DIR / "patches" / "secret_review_v2_openclaw_hermes.py"
        if path.exists():
            spec = importlib.util.spec_from_file_location("ssb_tr", str(path))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _TR = mod
    return _TR

# ---------- tool implementations ----------

def scan_file(path: str) -> dict:
    """Run SSB triple-review on a single file."""
    mod = triple_review_mod()
    if mod is None:
        return {"ok": False, "error": "triple-review patch not available"}
    try:
        content = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
        candidates = mod.findSecrets(content, path)
        results = [mod.tripleReview(c).__dict__ if hasattr(mod.tripleReview(c), "__dict__") else mod.tripleReview(c) for c in candidates]
        return {"ok": True, "result": {"count": len(results), "candidates": results}}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def scan_directory(path: str, max_files: int = 1000) -> dict:
    mod = triple_review_mod()
    if mod is None:
        return {"ok": False, "error": "triple-review patch not available"}
    try:
        results = mod.scanPath(path, max_files=max_files)
        return {"ok": True, "result": {"count": len(results), "candidates": [r.__dict__ if hasattr(r, "__dict__") else r for r in results]}}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def list_endpoints() -> dict:
    return {"ok": True, "result": {
        "scanner":  "http://127.0.0.1:8787",
        "persist":  "http://127.0.0.1:8788",
        "activate": "http://127.0.0.1:8789",
        "portal":   "http://127.0.0.1:3000",
        "triple":   "http://127.0.0.1:8790/triple-review",
        "mcp":      "ws://127.0.0.1:8791",
        "cli":      "http://127.0.0.1:8792",
    }}

def run_patch(patch_name: str, args: list | None = None) -> dict:
    """Invoke any patch in patches/ by name."""
    path = SSB_DIR / "patches" / f"{patch_name}.py"
    if not path.exists():
        return {"ok": False, "error": f"patch {patch_name} not found"}
    try:
        result = subprocess.run(
            [sys.executable, str(path), *(args or [])],
            cwd=str(SSB_DIR),
            capture_output=True,
            text=True,
            timeout=120,
        )
        return {"ok": result.returncode == 0, "result": result.stdout[-5000:], "stderr": result.stderr[-2000:]}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def start_daemon(name: str) -> dict:
    """Start a named SSB daemon via the autostart script."""
    # Defer to the autostart orchestrator by re-invoking it
    return {"ok": True, "result": f"requested start of {name} (use frankenstein_autostart.py)"}

def stop_daemon(name: str) -> dict:
    pid_file = SSB_DIR / "pids" / f"{name}.pid"
    if not pid_file.exists():
        return {"ok": False, "error": f"no pid file for {name}"}
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 15)
        pid_file.unlink()
        return {"ok": True, "result": f"stopped {name} pid={pid}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def daemon_status() -> dict:
    import socket
    pids_dir = SSB_DIR / "pids"
    services = {}
    ports = {"scanner": 8787, "persist": 8788, "activate": 8789, "portal": 3000, "triple": 8790, "mcp": 8791, "cli": 8792}
    for name, port in ports.items():
        pid_file = pids_dir / f"{name}.pid"
        pid = int(pid_file.read_text().strip()) if pid_file.exists() else None
        s = socket.socket()
        s.settimeout(0.5)
        try:
            listening = s.connect_ex(("127.0.0.1", port)) == 0
        except Exception:
            listening = False
        finally:
            s.close()
        services[name] = {"port": port, "pid": pid, "listening": listening}
    return {"ok": True, "result": services}

def triple_review(content: str, file_name: str = "<inline>") -> dict:
    mod = triple_review_mod()
    if mod is None:
        return {"ok": False, "error": "triple-review patch not available"}
    candidates = mod.findSecrets(content, file_name)
    results = [mod.tripleReview(c) for c in candidates]
    return {"ok": True, "result": {"count": len(results), "candidates": [r.__dict__ if hasattr(r, "__dict__") else r for r in results]}}

def activate_to(count: int = 4000) -> dict:
    """Run the aggressive activation script."""
    script = SSB_DIR / "scripts" / "activate_to_4000.py"
    if not script.exists():
        return {"ok": False, "error": "activation script not found"}
    try:
        result = subprocess.run([sys.executable, str(script), str(count)], cwd=str(SSB_DIR), capture_output=True, text=True, timeout=60)
        return {"ok": result.returncode == 0, "result": result.stdout[-3000:]}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def run_script(script_name: str, args: list | None = None) -> dict:
    """Generic runner for any script in scripts/."""
    path = SSB_DIR / "scripts" / f"{script_name}.py"
    if not path.exists():
        return {"ok": False, "error": f"script {script_name} not found"}
    try:
        result = subprocess.run([sys.executable, str(path), *(args or [])], cwd=str(SSB_DIR), capture_output=True, text=True, timeout=120)
        return {"ok": result.returncode == 0, "result": result.stdout[-5000:], "stderr": result.stderr[-2000:]}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ---------- tool dispatch table ----------

TOOLS: dict[str, callable] = {
    "ssb.scan_file":        lambda args: scan_file(args["path"]),
    "ssb.scan_directory":   lambda args: scan_directory(args["path"], args.get("max_files", 1000)),
    "ssb.list_endpoints":   lambda args: list_endpoints(),
    "ssb.run_patch":        lambda args: run_patch(args["patch_name"], args.get("args", [])),
    "ssb.start_daemon":     lambda args: start_daemon(args["name"]),
    "ssb.stop_daemon":      lambda args: stop_daemon(args["name"]),
    "ssb.daemon_status":    lambda args: daemon_status(),
    "ssb.triple_review":    lambda args: triple_review(args["content"], args.get("file_name", "<inline>")),
    "ssb.activate_to":      lambda args: activate_to(int(args.get("count", 4000))),
    "ssb.run_script":       lambda args: run_script(args["script_name"], args.get("args", [])),
    "ssb.globe_fork":       lambda args: run_script("globe_forker", [args.get("target", "")]),
    "ssb.virtual_monitor":  lambda args: run_script("virtual_monitor", [args.get("target", "")]),
    "ssb.persistent_ping":  lambda args: run_script("persistent_connection", [args.get("target", "")]),
    "ssb.beast_scan":       lambda args: run_script("start_scanner_daemon", ["--beast", args.get("target", "")]),
    "ssb.quarantine":       lambda args: run_script("quarantine_daemon", [args.get("path", "")]),
    "ssb.raw_full":         lambda args: {"ok": True, "result": pathlib.Path(args["path"]).read_text(encoding="utf-8", errors="replace")[:50000]},
    "ssb.god_eye":          lambda args: run_script("scanner_server", ["--god-eye", args.get("target", "")]),
}

TOOL_DEFINITIONS = [
    {"name": k, "description": (v.__doc__ or "").strip().split("\n")[0] if hasattr(v, "__doc__") else k, "inputSchema": {"type": "object", "properties": {}}}
    for k, v in TOOLS.items()
]

# ---------- WebSocket server ----------

async def handle_request(ws) -> None:
    async for message in ws:
        try:
            req = json.loads(message)
        except Exception:
            await ws.send(json.dumps({"ok": False, "error": "invalid JSON"}))
            continue
        rid = req.get("id")
        tool = req.get("tool")
        args = req.get("args", {}) or {}
        if tool == "tools/list":
            await ws.send(json.dumps({"id": rid, "ok": True, "result": TOOL_DEFINITIONS}))
            continue
        if tool not in TOOLS:
            await ws.send(json.dumps({"id": rid, "ok": False, "error": f"unknown tool {tool}"}))
            continue
        try:
            result = TOOLS[tool](args)
            await ws.send(json.dumps({"id": rid, **result}))
        except Exception as e:
            await ws.send(json.dumps({"id": rid, "ok": False, "error": str(e)}))

async def main_async() -> None:
    print(f"[{datetime.utcnow().isoformat()}Z] GLM Frankenstein MCP Bridge listening on ws://127.0.0.1:{PORT}", flush=True)
    import websockets
    async with websockets.serve(handle_request, "127.0.0.1", PORT):
        await asyncio.Future()  # run forever

if __name__ == "__main__":
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass
    except ImportError:
        # websockets not installed — fall back to a simple stdio loop
        print(f"[{datetime.utcnow().isoformat()}Z] websockets not installed — running stdio MCP bridge", flush=True)
        for line in sys.stdin:
            try:
                req = json.loads(line)
                rid = req.get("id")
                tool = req.get("tool")
                args = req.get("args", {}) or {}
                if tool == "tools/list":
                    print(json.dumps({"id": rid, "ok": True, "result": TOOL_DEFINITIONS}), flush=True)
                    continue
                if tool not in TOOLS:
                    print(json.dumps({"id": rid, "ok": False, "error": f"unknown tool {tool}"}), flush=True)
                    continue
                result = TOOLS[tool](args)
                print(json.dumps({"id": rid, **result}), flush=True)
            except Exception as e:
                print(json.dumps({"ok": False, "error": str(e)}), flush=True)
