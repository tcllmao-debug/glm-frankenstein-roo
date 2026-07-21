#!/usr/bin/env python3
"""
GLM Frankenstein — SSB Autostart Orchestrator
==============================================

Single entrypoint that boots the full SSB stack inside the GLM Frankenstein
VS Code extension. Called from TypeScript on extension activation.

Boots (in order, each in its own daemonized subprocess):
  1. Scanner daemon            — port 8787 (raw scanner endpoints)
  2. Persistent connection     — port 8788 (Z's persistent connection)
  3. Activation daemon         — port 8789 (node activation)
  4. Portal (Next.js)          — port 3000 (galaxy brain + scanner UI)
  5. Triple-review service     — port 8790 (OpenClaw + Hermes Beast Claw)
  6. MCP bridge                — port 8791 (Model Context Protocol over stdio+WS)
  7. CLI omni-runner           — exposes every CLI command via JSON-RPC on 8792

Endpoints (all under http://127.0.0.1):
  /                   portal home         :3000
  /scanner            scanner UI          :3000
  /api/state          node state JSON     :3000
  /api/raw?path=      raw file content    :3000
  /api/raw-full       8-method reader     :3000
  /api/god-eye        deep FS inspect     :3000
  /scan               raw scanner         :8787
  /beast/scan         beast scanner       :8787
  /triple-review      SSB triple review   :8790
  /mcp                MCP server          :8791
  /cli                CLI omni-runner     :8792

Skips the 400MB monolith (not bundled — too large for a VSIX).
All daemons are double-forked and write PIDs to <ssbDir>/pids/.
"""

from __future__ import annotations
import os, sys, json, time, socket, subprocess, signal, hashlib, pathlib, threading
from datetime import datetime

SSB_DIR = pathlib.Path(os.environ.get("SSB_DIR", "."))
PID_DIR = SSB_DIR / "pids"
LOG_DIR = SSB_DIR / "logs"
PID_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

PORTS = {
    "scanner":   8787,
    "persist":   8788,
    "activate":  8789,
    "portal":    3000,
    "triple":    8790,
    "mcp":       8791,
    "cli":       8792,
}

# ---------- helpers ----------

def log(msg: str) -> None:
    ts = datetime.utcnow().isoformat() + "Z"
    print(f"[{ts}] {msg}", flush=True)

def port_open(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()

def wait_for_port(port: int, timeout: float = 8.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_open(port):
            return True
        time.sleep(0.25)
    return False

def write_pid(name: str, pid: int) -> None:
    (PID_DIR / f"{name}.pid").write_text(str(pid))

def read_pid(name: str) -> int | None:
    f = PID_DIR / f"{name}.pid"
    if not f.exists():
        return None
    try:
        return int(f.read_text().strip())
    except Exception:
        return None

def kill_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except Exception:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass

# ---------- daemon launchers ----------

def launch_python_daemon(name: str, script: str, args: list[str] | None = None) -> int | None:
    """Daemonize a Python script (double-fork)."""
    script_path = SSB_DIR / script
    if not script_path.exists():
        log(f"  skip {name}: script {script} not found")
        return None

    # Use a simple Popen with start_new_session — sufficient on Linux/Mac
    log_path = LOG_DIR / f"{name}.log"
    logf = open(log_path, "ab", buffering=0)
    try:
        proc = subprocess.Popen(
            [sys.executable, str(script_path), *(args or [])],
            stdout=logf,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=str(SSB_DIR),
            start_new_session=True,
            env={**os.environ, "SSB_DIR": str(SSB_DIR), "PYTHONUNBUFFERED": "1"},
        )
        write_pid(name, proc.pid)
        log(f"  started {name} pid={proc.pid} -> {script} {args or []}")
        return proc.pid
    except Exception as e:
        log(f"  FAILED to start {name}: {e}")
        return None

def launch_triple_review_server() -> int | None:
    """Triple-review server: small HTTP server exposing /triple-review."""
    server_script = SSB_DIR / "frankenstein_triple_review_server.py"
    return launch_python_daemon("triple", str(server_script) if server_script.exists() else "patches/secret_review_v2_openclaw_hermes.py", ["--server", "--port", str(PORTS["triple"])])

def launch_mcp_bridge() -> int | None:
    """MCP bridge: stdio+WS server that exposes SSB tools as MCP tools."""
    bridge_script = SSB_DIR / "frankenstein_mcp_bridge.py"
    return launch_python_daemon("mcp", str(bridge_script), ["--port", str(PORTS["mcp"])])

def launch_cli_omni() -> int | None:
    """CLI omni-runner: JSON-RPC server on 8792 exposing every CLI command."""
    return launch_python_daemon("cli", "scripts/cli_api_omni.py", ["--port", str(PORTS["cli"])])

def launch_scanner() -> int | None:
    return launch_python_daemon("scanner", "scripts/start_scanner_daemon.py")

def launch_persistent() -> int | None:
    return launch_python_daemon("persist", "scripts/persistent_connection.py")

def launch_activation() -> int | None:
    return launch_python_daemon("activate", "scripts/start_activation_daemon.py")

def launch_portal() -> int | None:
    """Portal: Next.js frontend on port 3000. Skips if node/bun not available."""
    portal_dir = SSB_DIR / "frontend"
    if not portal_dir.exists():
        log("  skip portal: frontend/ dir not found")
        return None
    if not (portal_dir / "node_modules").exists():
        log("  skip portal: frontend/node_modules missing (run `npm install` in frontend/)")
        return None
    log_path = LOG_DIR / "portal.log"
    logf = open(log_path, "ab", buffering=0)
    try:
        # Try bun first, fall back to npm
        runner = None
        for candidate in ("bun", "npm"):
            try:
                subprocess.check_call([candidate, "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                runner = candidate
                break
            except Exception:
                continue
        if not runner:
            log("  skip portal: neither bun nor npm available")
            return None
        cmd = [runner, "run", "dev"] if runner == "npm" else [runner, "dev"]
        proc = subprocess.Popen(
            cmd,
            cwd=str(portal_dir),
            stdout=logf,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env={**os.environ, "PORT": str(PORTS["portal"])},
        )
        write_pid("portal", proc.pid)
        log(f"  started portal pid={proc.pid}")
        return proc.pid
    except Exception as e:
        log(f"  FAILED to start portal: {e}")
        return None

# ---------- status / health ----------

def status() -> dict:
    out = {"timestamp": datetime.utcnow().isoformat() + "Z", "services": {}}
    for name, port in PORTS.items():
        pid = read_pid(name)
        out["services"][name] = {
            "port": port,
            "pid": pid,
            "listening": port_open(port),
            "log_file": str(LOG_DIR / f"{name}.log"),
        }
    return out

def stop_all() -> None:
    for name in PORTS.keys():
        pid = read_pid(name)
        if pid:
            log(f"  stopping {name} pid={pid}")
            kill_pid(pid)
            (PID_DIR / f"{name}.pid").unlink(missing_ok=True)

# ---------- main ----------

def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "start"
    if cmd == "stop":
        stop_all()
        print(json.dumps({"stopped": True}, indent=2))
        return 0
    if cmd == "status":
        print(json.dumps(status(), indent=2))
        return 0
    if cmd == "restart":
        stop_all()
        time.sleep(1)
        return main_with_args(["start"])

    # default: start
    log(f"GLM Frankenstein SSB Autostart — booting stack from {SSB_DIR}")
    log(f"  ports: {PORTS}")

    # Order matters: scanner first (raw data), then persist, activate, triple, mcp, cli, portal last
    launch_scanner()
    launch_persistent()
    launch_activation()
    launch_triple_review_server()
    launch_mcp_bridge()
    launch_cli_omni()
    launch_portal()

    # Give everything a moment to bind
    time.sleep(1.5)
    log("Stack boot initiated. Status:")
    print(json.dumps(status(), indent=2))

    # Persist a status file the TS extension can read on activation
    (SSB_DIR / "frankenstein_status.json").write_text(json.dumps(status(), indent=2))
    return 0


def main_with_args(argv: list[str]) -> int:
    if len(argv) > 1:
        sys.argv = [sys.argv[0], *argv[1:]]
    return main()


if __name__ == "__main__":
    sys.exit(main())
