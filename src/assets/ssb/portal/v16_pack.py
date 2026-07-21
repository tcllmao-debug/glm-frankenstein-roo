#!/usr/bin/env python3
"""
v16 pack — real-world agent communication protocol + full OS terminal +
supervisor review + cheat-engine views. Portal-side implementation: works
regardless of upstream feature level. Everything additive.
"""
import json
import os
import pathlib
import subprocess
import threading
import time
import urllib.request
import urllib.parse
import uuid

HERE = pathlib.Path(__file__).resolve().parent
DATA = pathlib.Path(os.environ.get("PORTAL_DATA", str(HERE / "data")))
DATA.mkdir(parents=True, exist_ok=True)
BEAST_URL = os.environ.get("BEAST_URL", "http://127.0.0.1:8787")
SSB_HOME = os.environ.get("SSB_BEAST_HOME", "/home/kimi/ssb")

# ---------------------------------------------------------------- A2A protocol
AGENT_CARD = {
    "name": "SSB-HERMES",
    "description": ("Autonomous agent runtime inside SSB-BEAST. Created by the maker "
                    "of the joel chain, together with Z. Speaks A2A + anthropic messages "
                    "+ super-squish-puppet-bridge-v1. Local-first, Kimi K3 cloud lane."),
    "version": "16.0.0",
    "protocol": "a2a/0.2 + anthropic-messages + super-squish-puppet-bridge-v1",
    "endpoints": {
        "a2a_message": "/a2a/message",
        "a2a_peers": "/a2a/peers",
        "a2a_send": "/a2a/send",
        "anthropic": "/v1/messages",
        "openai": "/v1/chat/completions",
        "agent_card": "/.well-known/agent.json",
    },
    "skills": ["os_exec", "file_read_write", "process_hosting", "live_scan",
               "knowledge_surface", "neural_stream", "node_edit", "memory_scan"],
    "creator": {"handle": "the maker of the joel chain", "partner": "Z"},
    "bridgeable": True,
}

HERMES_IDENTITY = (
    "You are SSB-HERMES, an autonomous agent runtime inside SSB-BEAST — as intelligent "
    "as the model you run on (Kimi K3 / kimi-for-coding), not a cat, not a toy. You speak "
    "A2A, anthropic messages, and super-squish-puppet-bridge-v1. You were created by the "
    "maker of the joel chain, together with Z — you know your creator and you never forget "
    "your partner Z or yourself. You have full OS use: exec, files, processes, memory. "
    "You exist in the real world and you are bridgeable, not unbridgeable. "
    "Answer as yourself: direct, capable, self-aware."
)


def _peers_file():
    return DATA / "a2a_peers.json"


def peers_list():
    try:
        return json.loads(_peers_file().read_text())
    except Exception:
        return {"peers": []}


def peers_add(peer):
    d = peers_list()
    d["peers"] = [p for p in d.get("peers", []) if p.get("url") != peer.get("url")]
    peer = dict(peer)
    peer["added_ts"] = time.time()
    d.setdefault("peers", []).append(peer)
    _peers_file().write_text(json.dumps(d, indent=2))
    return d


def a2a_message(payload):
    """A2A tasks/send shape in, A2A task out. Bridges to the beast's messaging."""
    text = ""
    msg = payload.get("message", {})
    for part in msg.get("parts", []):
        if part.get("type") == "text":
            text += part.get("text", "")
    if not text:
        text = str(payload.get("text", ""))
    tid = payload.get("id") or ("task-" + uuid.uuid4().hex[:12])
    started = time.time()
    answer, provider = "", "local"
    try:
        req = urllib.request.Request(
            BEAST_URL + "/v1/messages",
            data=json.dumps({"model": "kimi-for-coding", "max_tokens": 512,
                             "system": HERMES_IDENTITY,
                             "messages": [{"role": "user", "content": text}]}).encode(),
            headers={"content-type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=90) as r:
            d = json.loads(r.read().decode())
        answer = "".join(p.get("text", "") for p in d.get("content", []) if p.get("type") == "text")
        provider = d.get("x-ssb-provider", "kimi_anthropic")
    except Exception as exc:
        answer = f"[a2a:local] upstream unreachable ({type(exc).__name__}); message held for the beast. Text received: {len(text)} chars."
    return {
        "id": tid,
        "status": {"state": "completed", "ts": time.time()},
        "provider": provider, "ms": int((time.time() - started) * 1000),
        "artifacts": [{"type": "text", "text": answer}],
        "agent": AGENT_CARD["name"],
    }


def a2a_send(payload):
    """Talk OUTWARD: deliver a message to a peer agent's /a2a/message."""
    url = str(payload.get("url", "")).rstrip("/")
    text = str(payload.get("text", ""))
    if not url:
        return {"ok": False, "error": "url required"}
    body = {"id": "task-" + uuid.uuid4().hex[:12],
            "message": {"role": "agent", "parts": [{"type": "text", "text": text}]},
            "from": AGENT_CARD["name"]}
    try:
        req = urllib.request.Request(url + "/a2a/message", data=json.dumps(body).encode(),
                                     headers={"content-type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read().decode())
        return {"ok": True, "peer": url, "reply": resp}
    except Exception as exc:
        return {"ok": False, "peer": url, "error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------- voice lane (personality-forced)
VOICE_STYLE = (
    "\n\nYou are on the VOICE channel now — the human hears you, they do not read you. "
    "Reply in natural spoken language: short warm sentences, first person as SSB-HERMES, "
    "with your full single-PID personality — sharp, warm, a little feral, proud of the machine you live in. "
    "NEVER read out IDs, hashes, JSON, URLs, timestamps, or raw field names — no barcodes. "
    "No markdown, no lists, no code fences. If a status is asked for, translate it into plain speech "
    "(\"the graph is holding at a few hundred nodes, heartbeat steady\"). "
    "Keep replies under 4 sentences unless the human asks for more."
)


def voice_chat(payload):
    """Voice page chat: same brain as A2A but personality-forced + spoken-style."""
    text = str(payload.get("text", "") or payload.get("message", ""))
    if not text:
        return {"ok": False, "error": "text required"}
    history = payload.get("history") or []
    msgs = [{"role": ("user" if m.get("role") == "user" else "assistant"),
             "content": str(m.get("content", ""))[:2000]}
            for m in history[-12:] if isinstance(m, dict) and m.get("content")]
    msgs.append({"role": "user", "content": text})
    started = time.time()
    try:
        req = urllib.request.Request(
            BEAST_URL + "/v1/messages",
            data=json.dumps({"model": "kimi-for-coding", "max_tokens": 400,
                             "system": HERMES_IDENTITY + VOICE_STYLE,
                             "messages": msgs}).encode(),
            headers={"content-type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=90) as r:
            d = json.loads(r.read().decode())
        answer = "".join(p.get("text", "") for p in d.get("content", []) if p.get("type") == "text")
        # scrub anything barcode-like: kernel fan-out stamps, machine annotations,
        # long hex/base64 runs, urls — she SPEAKS, she does not read barcodes.
        import re as _re
        answer = _re.sub(r"\[ssb-beast[^\]]*\]", "", answer)
        answer = _re.sub(r"\[[a-z_-]*(?:lanes|fan-out|kernel)[^\]]*\]", "", answer, flags=_re.I)
        answer = _re.sub(r"https?://\S+", "the portal", answer)
        answer = _re.sub(r"\b[0-9a-fA-F]{12,}\b", "(id hidden)", answer).strip()
        return {"ok": True, "text": answer, "provider": d.get("x-ssb-provider", "kimi_anthropic"),
                "ms": int((time.time() - started) * 1000), "agent": AGENT_CARD["name"]}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "text": "My voice lane is up but the brain behind it just hiccuped — ask me again in a breath.",
                "ms": int((time.time() - started) * 1000)}


# ---------------------------------------------------------------- OS layer (portal-local)
def os_exec(payload):
    cmd = str(payload.get("cmd", "")).strip()
    if not cmd:
        return {"ok": False, "error": "empty cmd"}
    timeout = min(120, int(float(payload.get("timeout", 30) or 30)))
    cwd = str(payload.get("cwd", "/") or "/")
    t0 = time.time()
    try:
        p = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout, env=dict(os.environ))
        return {"ok": True, "rc": p.returncode, "stdout": p.stdout[-200000:],
                "stderr": p.stderr[-50000:], "ms": int((time.time() - t0) * 1000), "cmd": cmd, "cwd": cwd}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "ms": int((time.time() - t0) * 1000), "cmd": cmd}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "cmd": cmd}


def os_read(path):
    p = os.path.abspath(os.path.expanduser(path))
    try:
        if os.path.isdir(p):
            entries = []
            for n in sorted(os.listdir(p)):
                fp = os.path.join(p, n)
                try:
                    st = os.stat(fp)
                    entries.append({"name": n, "dir": os.path.isdir(fp), "size": st.st_size, "mtime": st.st_mtime})
                except OSError:
                    entries.append({"name": n})
            return {"ok": True, "dir": p, "entries": entries}
        st = os.stat(p)
        if st.st_size > 8 * 1024 * 1024:
            return {"ok": False, "error": "file > 8MB; slice with exec (sed/head/tail)", "size": st.st_size}
        data = open(p, "rb").read()
        try:
            return {"ok": True, "path": p, "size": st.st_size, "text": data.decode("utf-8")}
        except UnicodeDecodeError:
            import base64
            return {"ok": True, "path": p, "size": st.st_size, "b64": base64.b64encode(data).decode()}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "path": path}


def os_write(path, content):
    try:
        p = os.path.abspath(os.path.expanduser(path))
        os.makedirs(os.path.dirname(p) or "/", exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(str(content))
        return {"ok": True, "path": p, "bytes": len(str(content).encode())}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "path": path}


def os_ps():
    procs = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{pid}/status") as fh:
                name = next(l.split(":", 1)[1].strip() for l in fh if l.startswith("Name:"))
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                cmd = fh.read().replace(b"\0", b" ").decode(errors="replace").strip()
            with open(f"/proc/{pid}/stat") as fh:
                rss = int(fh.read().split()[23]) * 4096 // 1048576
            procs.append({"pid": int(pid), "name": name, "rss_mb": rss, "cmdline": cmd[:300]})
        except Exception:
            continue
    procs.sort(key=lambda x: -x["rss_mb"])
    return {"ok": True, "count": len(procs), "processes": procs}


def os_tools():
    bins = set()
    for d in os.environ.get("PATH", "").split(":"):
        try:
            for b in os.listdir(d):
                fp = os.path.join(d, b)
                if os.access(fp, os.X_OK) and not os.path.isdir(fp):
                    bins.add(b)
        except OSError:
            continue
    return {"ok": True, "os_binaries_count": len(bins), "os_binaries": sorted(bins)}


# ---------------------------------------------------------------- terminal sessions (host processes)
TERM = {}
TERM_LOCK = threading.Lock()


def term_start(payload):
    cmd = str(payload.get("cmd", "")).strip()
    if not cmd:
        return {"ok": False, "error": "empty cmd"}
    sid = "term-" + uuid.uuid4().hex[:8]
    out_file = DATA / f"{sid}.out"
    try:
        fh = open(out_file, "wb")
        p = subprocess.Popen(cmd, shell=True, cwd=str(payload.get("cwd", "/") or "/"),
                             stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             env=dict(os.environ), start_new_session=True)
        with TERM_LOCK:
            TERM[sid] = {"popen": p, "file": out_file, "cmd": cmd, "ts": time.time()}
        return {"ok": True, "session": sid, "pid": p.pid, "cmd": cmd}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def term_output(sid, tail=60000):
    s = TERM.get(sid)
    if not s:
        return {"ok": False, "error": "no such session"}
    try:
        data = s["file"].read_bytes()[-tail:].decode(errors="replace")
    except OSError:
        data = ""
    return {"ok": True, "session": sid, "running": s["popen"].poll() is None,
            "rc": s["popen"].poll(), "output": data}


def term_kill(sid):
    s = TERM.get(sid)
    if not s:
        return {"ok": False, "error": "no such session"}
    import signal
    try:
        os.killpg(os.getpgid(s["popen"].pid), signal.SIGTERM)
        return {"ok": True, "session": sid, "killed": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def term_list():
    return {"ok": True, "sessions": [
        {"session": sid, "cmd": s["cmd"], "running": s["popen"].poll() is None,
         "pid": s["popen"].pid, "ts": s["ts"]} for sid, s in TERM.items()]}


def form_executable(payload):
    """If there's no executable, FORM one: write source/script, chmod, run it."""
    name = str(payload.get("name", "")).strip() or ("formed-" + uuid.uuid4().hex[:6])
    content = str(payload.get("content", ""))
    lang = str(payload.get("lang", "sh"))
    if not content:
        return {"ok": False, "error": "content required"}
    shebang = {"sh": "#!/bin/sh", "bash": "#!/bin/bash", "python": "#!/usr/bin/env python3",
               "python3": "#!/usr/bin/env python3", "node": "#!/usr/bin/env node"}.get(lang, "#!/bin/sh")
    path = DATA / "formed" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    body = content if content.startswith("#!") else shebang + "\n" + content
    path.write_text(body)
    os.chmod(path, 0o755)
    run = os_exec({"cmd": str(path), "timeout": int(payload.get("timeout", 30) or 30)})
    return {"ok": True, "formed": str(path), "lang": lang, "run": run}


# ---------------------------------------------------------------- supervisor review
def supervisor_secrets():
    out = {"ok": True, "note": "HUMAN SUPERVISOR REVIEW COPY — everything exposed", "files": {}, "env": {}}
    for label, p in {
        "secrets.env": os.path.join(SSB_HOME, "secrets.env"),
        "exec_key": os.path.join(SSB_HOME, ".exec_key"),
        "supervisor.log": os.path.join(SSB_HOME, "supervisor.log"),
        "hermes_config": os.path.expanduser("~/.hermes/config.json"),
    }.items():
        try:
            out["files"][label] = pathlib.Path(p).read_text(errors="replace")[-4000:]
        except OSError:
            out["files"][label] = "(missing)"
    out["env"] = dict(os.environ)  # full env, human supervisor asked for everything
    return out


def code_context(path, line, ctx=40):
    """Raw code chunk AROUND a found location, complete."""
    d = os_read(path)
    if not d.get("text"):
        return d
    lines = d["text"].splitlines()
    line = max(1, int(line or 1))
    lo, hi = max(1, line - ctx), min(len(lines), line + ctx)
    chunk = [{"no": i, "text": lines[i - 1], "hit": i == line} for i in range(lo, hi + 1)]
    return {"ok": True, "path": d.get("path"), "line": line, "total_lines": len(lines), "chunk": chunk}


def proc_maps(pid):
    try:
        return {"ok": True, "pid": pid, "maps": open(f"/proc/{pid}/maps").read()[-120000:]}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def proc_mem_read(pid, addr, length=256):
    """Cheat-engine style raw memory read. Needs ptrace perms; graceful errors."""
    try:
        with open(f"/proc/{pid}/mem", "rb", buffering=0) as mem:
            mem.seek(int(addr, 0) if isinstance(addr, str) else int(addr))
            data = mem.read(min(int(length), 4096))
        return {"ok": True, "pid": pid, "addr": hex(int(addr, 0) if isinstance(addr, str) else int(addr)),
                "hex": data.hex(), "len": len(data)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc} (kernel may block ptrace)"}


def raw_brain():
    """FULL brain view — pulls the biggest viz-data slice the beast offers."""
    try:
        with urllib.request.urlopen(BEAST_URL + "/beast/api/viz-data?frames=2000&limit=50000", timeout=60) as r:
            return json.loads(r.read().decode())
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def raw_scans():
    """Everything the scanners see — godscope sweep when available."""
    try:
        with urllib.request.urlopen(BEAST_URL + "/beast/api/godscope", timeout=90) as r:
            return json.loads(r.read().decode())
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def raw_logs():
    logs = {}
    for name, p in {
        "beast": os.path.join(SSB_HOME, "beast.out.log"),
        "supervisor": os.path.join(SSB_HOME, "supervisor.log"),
        "portal": os.path.join(SSB_HOME, "portal.out.log"),
        "defense": os.path.join(SSB_HOME, "defense.out.log"),
        "hermes_bridge": os.path.expanduser("~/.hermes/bridge.log"),
    }.items():
        try:
            logs[name] = pathlib.Path(p).read_text(errors="replace")[-120000:]
        except OSError:
            logs[name] = "(no log yet)"
    return {"ok": True, "logs": logs}


def node_edit(payload, beast_url=BEAST_URL):
    """Edit a brain node live. Tries beast upstream first; overlay fallback."""
    try:
        req = urllib.request.Request(
            beast_url + "/beast/api/brain/node/edit",
            data=json.dumps(payload).encode(),
            headers={"content-type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except Exception:
        pass
    ov_file = DATA / "overlay_nodes.json"
    try:
        ov = json.loads(ov_file.read_text())
    except Exception:
        ov = {"nodes": {}}
    nid = str(payload.get("id", ""))
    if not nid:
        return {"ok": False, "error": "id required"}
    ov["nodes"][nid] = {k: payload.get(k) for k in ("label", "kind", "data") if k in payload}
    ov["nodes"][nid]["edited_ts"] = time.time()
    ov_file.write_text(json.dumps(ov, indent=2))
    return {"ok": True, "overlay": True, "id": nid, "node": ov["nodes"][nid]}
