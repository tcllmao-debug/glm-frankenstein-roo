#!/usr/bin/env python3
"""GLM Frankenstein — SSB Autostart Orchestrator. Boots all SSB daemons."""
from __future__ import annotations
import os, sys, json, time, socket, subprocess, signal, pathlib
from datetime import datetime

SSB_DIR = pathlib.Path(os.environ.get("SSB_DIR", "."))
PID_DIR = SSB_DIR / "pids"
LOG_DIR = SSB_DIR / "logs"
PID_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

PORTS = {"scanner": 8787, "persist": 8788, "activate": 8789, "portal": 3000, "triple": 8790, "mcp": 8791, "cli": 8792}

def log(msg): print(f"[{datetime.utcnow().isoformat()}Z] {msg}", flush=True)
def port_open(port):
    s = socket.socket(); s.settimeout(0.5)
    try: return s.connect_ex(("127.0.0.1", port)) == 0
    finally: s.close()
def write_pid(name, pid): (PID_DIR / f"{name}.pid").write_text(str(pid))
def read_pid(name):
    f = PID_DIR / f"{name}.pid"
    try: return int(f.read_text().strip()) if f.exists() else None
    except: return None
def kill_pid(pid):
    try: os.kill(pid, signal.SIGTERM)
    except: pass

def launch_python_daemon(name, script, args=None):
    script_path = SSB_DIR / script
    if not script_path.exists():
        log(f"  skip {name}: {script} not found"); return None
    logf = open(LOG_DIR / f"{name}.log", "ab", buffering=0)
    try:
        proc = subprocess.Popen([sys.executable, str(script_path), *(args or [])],
            stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            cwd=str(SSB_DIR), start_new_session=True,
            env={**os.environ, "SSB_DIR": str(SSB_DIR), "PYTHONUNBUFFERED": "1"})
        write_pid(name, proc.pid)
        log(f"  started {name} pid={proc.pid}")
        return proc.pid
    except Exception as e:
        log(f"  FAILED {name}: {e}"); return None

def launch_triple_review_server():
    return launch_python_daemon("triple", "patches/secret_review_v2_openclaw_hermes.py", ["--server", "--port", str(PORTS["triple"])])
def launch_mcp_bridge():
    return launch_python_daemon("mcp", "frankenstein_mcp_bridge.py", ["--port", str(PORTS["mcp"])])
def launch_cli_omni():
    return launch_python_daemon("cli", "scripts/cli_api_omni.py", ["--port", str(PORTS["cli"])])

def launch_portal():
    portal_dir = SSB_DIR / "frontend"
    if not portal_dir.exists() or not (portal_dir / "node_modules").exists():
        log("  skip portal: frontend/node_modules missing"); return None
    logf = open(LOG_DIR / "portal.log", "ab", buffering=0)
    runner = None
    for c in ("bun", "npm"):
        try:
            subprocess.check_call([c, "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            runner = c; break
        except: pass
    if not runner: log("  skip portal: no bun/npm"); return None
    cmd = [runner, "run", "dev"] if runner == "npm" else [runner, "dev"]
    proc = subprocess.Popen(cmd, cwd=str(portal_dir), stdout=logf, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, start_new_session=True, env={**os.environ, "PORT": str(PORTS["portal"])})
    write_pid("portal", proc.pid); return proc.pid

def status():
    out = {"timestamp": datetime.utcnow().isoformat() + "Z", "services": {}}
    for name, port in PORTS.items():
        out["services"][name] = {"port": port, "pid": read_pid(name), "listening": port_open(port), "log_file": str(LOG_DIR / f"{name}.log")}
    return out

def stop_all():
    for name in PORTS:
        pid = read_pid(name)
        if pid: log(f"  stopping {name} pid={pid}"); kill_pid(pid); (PID_DIR / f"{name}.pid").unlink(missing_ok=True)

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "start"
    if cmd == "stop": stop_all(); print(json.dumps({"stopped": True}, indent=2)); return 0
    if cmd == "status": print(json.dumps(status(), indent=2)); return 0
    log(f"GLM Frankenstein SSB Autostart — booting stack from {SSB_DIR}")
    launch_python_daemon("scanner", "scripts/start_scanner_daemon.py")
    launch_python_daemon("persist", "scripts/persistent_connection.py")
    launch_python_daemon("activate", "scripts/start_activation_daemon.py")
    launch_triple_review_server()
    launch_mcp_bridge()
    launch_cli_omni()
    launch_portal()
    time.sleep(1.5)
    log("Stack boot initiated. Status:")
    print(json.dumps(status(), indent=2))
    (SSB_DIR / "frankenstein_status.json").write_text(json.dumps(status(), indent=2))
    return 0

if __name__ == "__main__":
    sys.exit(main())
