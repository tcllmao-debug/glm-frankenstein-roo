#!/usr/bin/env python3
"""
HERMES bridge daemon — speaks super-squish-puppet-bridge-v1.
Install parity with the OpenClaw bridge: same protocol, same file-drop dirs,
same methods. The SSB-BEAST PuppetBridge spawns this via HERMES_BRIDGE_COMMAND.

Wire protocol (stdin/stdout, one JSON object per line):
  <- {"id": "...", "method": "generate", "params": {"prompt": ..., "system": ..., "task": ...}}
  -> {"id": "...", "ok": true, "text": "...", "agent": "hermes", "provider": "kimi_anthropic"}

Methods: generate | status | pending | agent_context | respond | ping
Routing: KIMI_API_KEY present -> Kimi K3 (kimi-for-coding, anthropic dialect).
         otherwise -> local offline brain echo (runs local, always available).
Env:
  KIMI_API_KEY / KIMI_API_KEY_FILE / KIMI_BASE_URL (default https://api.kimi.com/coding)
  KIMI_MODEL (default kimi-for-coding)   HERMES_HOME (default ~/.hermes)
"""
import json
import os
import pathlib
import sys
import time
import traceback
import urllib.request

HERMES_HOME = pathlib.Path(os.environ.get("HERMES_HOME", str(pathlib.Path.home() / ".hermes")))
HERMES_HOME.mkdir(parents=True, exist_ok=True)
(HERMES_HOME / "memory").mkdir(exist_ok=True)
(HERMES_HOME / "sessions").mkdir(exist_ok=True)
LOG = HERMES_HOME / "bridge.log"

BOOT_TS = time.time()
PENDING = []  # file-drop requests observed but not yet answered


def log(msg):
    try:
        with LOG.open("a") as fh:
            fh.write(time.strftime("%Y-%m-%dT%H:%M:%S") + " " + msg + "\n")
    except OSError:
        pass


def _api_key():
    key = os.environ.get("KIMI_API_KEY", "").strip()
    if not key:
        kf = os.environ.get("KIMI_API_KEY_FILE", "").strip()
        if kf and pathlib.Path(kf).exists():
            key = pathlib.Path(kf).read_text().strip()
    return key


def kimi_generate(prompt, system="", task="general", max_tokens=1024):
    """Call Kimi K3 via the anthropic-messages dialect. Returns {text, provider, usage}."""
    key = _api_key()
    if not key:
        return {
            "text": ("[hermes:local] KIMI_API_KEY not set — answering from the local "
                     "Hermes runtime. Prompt received (%d chars, task=%s). "
                     "Local autonomy loop remains active." % (len(prompt), task)),
            "provider": "hermes_local",
            "usage": {"input_tokens": len(prompt) // 4, "output_tokens": 24},
        }
    base = os.environ.get("KIMI_BASE_URL", "https://api.kimi.com/coding").rstrip("/")
    model = os.environ.get("KIMI_MODEL", "kimi-for-coding")
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        body["system"] = system
    req = urllib.request.Request(
        base + "/v1/messages",
        data=json.dumps(body).encode(),
        headers={
            "content-type": "application/json",
            "x-api-key": key,
            "authorization": "Bearer " + key,
            "anthropic-version": "2023-06-01",
            "x-coding-agent": "claude-code",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode())
    text = "".join(part.get("text", "") for part in data.get("content", []) if part.get("type") == "text")
    return {"text": text or "(empty)", "provider": "kimi_anthropic", "usage": data.get("usage", {})}


def agent_context():
    ctx = {
        "agent": "hermes",
        "hermes_home": str(HERMES_HOME),
        "openclaw_home": str(pathlib.Path.home() / ".openclaw"),
        "has_kimi_key": bool(_api_key()),
        "uptime_s": round(time.time() - BOOT_TS, 1),
        "detected": [],
    }
    for name in ("openclaw", "hermes", "miahou"):
        if (pathlib.Path.home() / ("." + name)).exists():
            ctx["detected"].append(name)
    return ctx


def status():
    return {
        "agent": "hermes",
        "ok": True,
        "uptime_s": round(time.time() - BOOT_TS, 1),
        "pending": len(PENDING),
        "provider": "kimi_anthropic" if _api_key() else "hermes_local",
        "memory_files": len(list((HERMES_HOME / "memory").glob("*"))),
    }


def handle(req):
    rid = req.get("id")
    method = req.get("method", "")
    params = req.get("params", {}) or {}
    try:
        if method == "generate":
            out = kimi_generate(params.get("prompt", ""), params.get("system", ""), params.get("task", "general"))
            resp = {"id": rid, "ok": True, "agent": "hermes"}
            resp.update(out)
            return resp
        if method == "status":
            resp = {"id": rid, "ok": True}
            resp.update(status())
            return resp
        if method == "pending":
            return {"id": rid, "ok": True, "pending": PENDING[: int(params.get("limit", 10))]}
        if method == "agent_context":
            resp = {"id": rid, "ok": True}
            resp.update(agent_context())
            return resp
        if method == "respond":
            PENDING.append({"id": rid, "ts": time.time(), "text": str(params.get("text", ""))[:2000]})
            return {"id": rid, "ok": True, "queued": len(PENDING)}
        if method == "ping":
            return {"id": rid, "ok": True, "pong": True, "ts": time.time()}
        return {"id": rid, "ok": False, "error": "unknown method: " + str(method)}
    except Exception as exc:  # never die on a bad request
        log("ERROR " + traceback.format_exc(limit=3))
        return {"id": rid, "ok": False, "error": str(exc)}


def handle_oneshot(req):
    """Flat PuppetBridge payload: {id, task, prompt, system, ...} -> single JSON doc on stdout."""
    rid = req.get("id")
    out = kimi_generate(req.get("prompt", ""), req.get("system", ""), req.get("task", "general"))
    resp = {"id": rid, "ok": True, "agent": "hermes", "model": os.environ.get("KIMI_MODEL", "kimi-for-coding")}
    resp.update(out)
    log("oneshot %s -> ok=%s provider=%s" % (rid, resp.get("ok"), resp.get("provider")))
    return resp


def main():
    log("hermes bridge boot pid=%s key=%s" % (os.getpid(), "yes" if _api_key() else "no"))
    first = True
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            sys.stdout.write(json.dumps({"ok": False, "error": "bad json"}) + "\n")
            sys.stdout.flush()
            continue
        # flat one-shot payload (monolith PuppetBridge subprocess.run contract):
        # single request on stdin, single response on stdout, then exit.
        if "prompt" in req and "method" not in req:
            sys.stdout.write(json.dumps(handle_oneshot(req)) + "\n")
            sys.stdout.flush()
            return 0
        if first:
            # daemon mode banner — only once we know the caller speaks the line protocol
            pass
        first = False
        resp = handle(req)
        log("%s -> ok=%s" % (req.get("method"), resp.get("ok")))
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
