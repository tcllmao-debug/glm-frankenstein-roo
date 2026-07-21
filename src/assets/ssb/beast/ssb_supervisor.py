#!/usr/bin/env python3
"""
SSB single-PID supervisor (ADDITIVE v14).

ONE process id owns the whole stack: beast brain (8787) + defense layer (8792)
+ portal (8080) + the Hermes autonomy loop (in-beast thread) + a constant
keepalive connection to the Kimi K3 API. If any child dies, the watchdog
restarts it. Another agent (or a human) can read supervisor.pid and
supervisor.log to see everything.

Env:
  SSB_SUPERVISOR_HOME   state dir (pid/log)     default: this script's dir
  BEAST_DIR             dir holding ssb_beast.py default: this script's dir
  DEFENSE_SCRIPTS       scanner_server dir      default: ../github/repo/system-wide-defense/scripts
  PORTAL_DIR            portal dir              default: ../portal
  SSB_KEEPALIVE_S       API keepalive seconds   default: 60 (0 disables)
  KIMI_API_KEY          read from env or secrets.env in SSB_SUPERVISOR_HOME
"""
import json
import os
import pathlib
import signal
import subprocess
import sys
import threading
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
HOME = pathlib.Path(os.environ.get("SSB_SUPERVISOR_HOME", str(HERE)))
BEAST_DIR = pathlib.Path(os.environ.get("BEAST_DIR", str(HERE)))
DEFENSE_SCRIPTS = pathlib.Path(os.environ.get(
    "DEFENSE_SCRIPTS", str(HERE.parent / "github/repo/system-wide-defense/scripts")))
PORTAL_DIR = pathlib.Path(os.environ.get("PORTAL_DIR", str(HERE.parent / "portal")))
KEEPALIVE_S = float(os.environ.get("SSB_KEEPALIVE_S", "60"))
PID_FILE = HOME / "supervisor.pid"
LOG_FILE = HOME / "supervisor.log"

CHILDREN = {}      # name -> Popen
CHILD_META = {}    # name -> (cmd, cwd, env_extra)
STOP = threading.Event()
RESTARTS = {}


def log(msg):
    line = "%s %s" % (time.strftime("%Y-%m-%dT%H:%M:%S"), msg)
    try:
        with LOG_FILE.open("a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    print(line, flush=True)


def load_secrets():
    for sec in (HOME / "secrets.env", BEAST_DIR / "secrets.env"):
        if sec.exists():
            for ln in sec.read_text().splitlines():
                if "=" in ln and not ln.strip().startswith("#"):
                    k, v = ln.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
            log("secrets loaded from %s (KIMI_API_KEY=%s)" % (sec, "set" if os.environ.get("KIMI_API_KEY") else "missing"))
            return
    log("no secrets.env found (KIMI_API_KEY from environment only)")


def define_children():
    beast_env = {
        "SSB_BEAST_HOME": str(BEAST_DIR),
        "SSB_MONOLITH_FILE_PIN": str(BEAST_DIR / "ssb_monolith_patched.py"),
        "SSB_BEAST_MSG_FANOUT_TIMEOUT": "12",
        "SSB_HERMES_MODE": "1",
        "SSB_HERMES_AUTONOMY": "1",
        "HERMES_BRIDGE_COMMAND": "%s %s" % (sys.executable, BEAST_DIR / "hermes/hermes_bridge.py"),
        "OPENCLAW_BRIDGE_COMMAND": "%s %s" % (sys.executable, BEAST_DIR / "hermes/hermes_bridge.py"),
    }
    CHILD_META["beast"] = (
        [sys.executable, "ssb_beast.py", "brain", "--host", "127.0.0.1", "--port", "8787",
         "--internal-port", "8791", "--workspace", "/tmp/ssb_ws"], str(BEAST_DIR), beast_env)
    CHILD_META["defense"] = (
        [sys.executable, "portable_boot.py"], str(DEFENSE_SCRIPTS), {"SSB_DEFENSE_PORT": "8792"})
    CHILD_META["portal"] = (
        [sys.executable, "portal_server.py"], str(PORTAL_DIR),
        {"PORTAL_PORT": os.environ.get("PORTAL_PORT", os.environ.get("PORT", "8080")),
         "BEAST_URL": "http://127.0.0.1:8787",
         "DEFENSE_URL": "http://127.0.0.1:8792",
         "PORTAL_DATA": str(PORTAL_DIR / "data")})


def start_child(name):
    cmd, cwd, extra = CHILD_META[name]
    if not pathlib.Path(cwd).exists():
        log("child %s skipped: %s missing" % (name, cwd))
        return
    env = dict(os.environ)
    env.update(extra)
    try:
        p = subprocess.Popen(cmd, cwd=cwd, env=env,
                             stdout=open(HOME / ("%s.out.log" % name), "ab"),
                             stderr=subprocess.STDOUT, start_new_session=True)
        CHILDREN[name] = p
        log("child %s started pid=%d" % (name, p.pid))
    except Exception as exc:
        log("child %s start failed: %s" % (name, exc))


def watchdog():
    while not STOP.is_set():
        for name, p in list(CHILDREN.items()):
            rc = p.poll()
            if rc is not None:
                RESTARTS[name] = RESTARTS.get(name, 0) + 1
                log("WATCHDOG: %s exited rc=%s — restart #%d" % (name, rc, RESTARTS[name]))
                time.sleep(min(30, 2 * RESTARTS[name]))
                start_child(name)
        STOP.wait(5.0)


def keepalive():
    """Constant connection for the single-PID agent: tiny authenticated ping to
    any endpoint that takes this key (Kimi K3 /v1/messages), plus local brain
    health — keeps both the cloud lane and the local lane warm."""
    key = os.environ.get("KIMI_API_KEY", "")
    base = os.environ.get("KIMI_BASE_URL", "https://api.kimi.com/coding").rstrip("/")
    while not STOP.is_set():
        if key:
            try:
                body = json.dumps({"model": os.environ.get("KIMI_MODEL", "kimi-for-coding"),
                                   "max_tokens": 1,
                                   "messages": [{"role": "user", "content": "ping"}]}).encode()
                req = urllib.request.Request(base + "/v1/messages", data=body, method="POST",
                                             headers={"content-type": "application/json",
                                                      "x-api-key": key, "authorization": "Bearer " + key,
                                                      "anthropic-version": "2023-06-01",
                                                      "x-coding-agent": "claude-code"})
                t0 = time.time()
                with urllib.request.urlopen(req, timeout=30) as resp:
                    resp.read()
                log("keepalive: kimi K3 OK %dms" % int((time.time() - t0) * 1000))
            except Exception as exc:
                log("keepalive: kimi lane %s (local lanes keep running)" % type(exc).__name__)
        try:
            with urllib.request.urlopen("http://127.0.0.1:8787/beast/api/health", timeout=10) as resp:
                h = json.loads(resp.read().decode())
            log("keepalive: local brain OK nodes=%s edges=%s" % (h.get("nodes"), h.get("edges")))
        except Exception as exc:
            log("keepalive: local brain %s" % type(exc).__name__)
        STOP.wait(KEEPALIVE_S)


def shutdown(*_):
    STOP.set()
    for name, p in CHILDREN.items():
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            log("shutdown: %s terminated" % name)
        except Exception:
            pass


def main():
    HOME.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))
    log("supervisor boot pid=%d keepalive=%ss" % (os.getpid(), KEEPALIVE_S))
    load_secrets()
    define_children()
    for name in ("beast", "defense", "portal"):
        start_child(name)
    threading.Thread(target=watchdog, name="ssb-watchdog", daemon=True).start()
    if KEEPALIVE_S > 0:
        threading.Thread(target=keepalive, name="ssb-keepalive", daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    while not STOP.is_set():
        STOP.wait(1.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
