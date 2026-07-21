#!/usr/bin/env python3
"""SSB Z-MARK COMPLETE PORTAL — one web URL wiring EVERY system together:

  /                    portal hub (live status cards for all systems)
  /beast/*             Beast Claw GodScope GUI + all beast APIs        -> BEAST_URL
  /legacy*             original Galaxy Brain V11 GUI                   -> BEAST_URL
  /v1/*                Anthropic-style messaging API                   -> BEAST_URL
  /api/*               original monolith APIs                          -> BEAST_URL
  /defense/*           system-wide-defense scanner_server (FastAPI)    -> DEFENSE_URL
  /neural-stream       live merged neural stream page (all sources)
  /neural/feed         merged live feed JSON (beast+monolith+defense)
  /portal/api/status   live status of every backend
  /data/*              baked populated-data snapshots (instant first paint)

Design: additive, stdlib only, never 404s on wired links (graceful backend-down pages).
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("PORTAL_PORT", os.environ.get("PORT", "8080")))
HOST = os.environ.get("PORTAL_HOST", "0.0.0.0")
BEAST_URL = os.environ.get("BEAST_URL", "http://127.0.0.1:8787")
DEFENSE_URL = os.environ.get("DEFENSE_URL", "http://127.0.0.1:8792")
DATA_DIR = Path(os.environ.get("PORTAL_DATA", str(Path(__file__).resolve().parent / "data")))
try:
    import v16_pack as V16
except Exception:
    V16 = None
STARTED = time.time()

HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
              "te", "trailers", "transfer-encoding", "upgrade"}


def _fetch(url: str, timeout: float = 8.0):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ssb-portal/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except Exception:
            body = b""
        return exc.code, dict(exc.headers or {}), body
    except Exception:
        return None, {}, b""


def _fetch_json(url: str, timeout: float = 8.0):
    status, _, body = _fetch(url, timeout)
    if status != 200 or not body:
        return None
    try:
        return json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return None


def _baked(name: str):
    p = DATA_DIR / name
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return None
    return None


SYSTEMS = [
    {"id": "beast", "name": "BEAST CLAW — GodScope GUI", "url": "/beast/",
     "desc": "10 visualizations incl. 4D particle frame scanner, knowledge surface, node inspector, wedge process watcher, MESSAGES console, LIVE API strip.",
     "health": "/beast/api/health", "tag": "8787"},
    {"id": "legacy", "name": "LEGACY — Galaxy Brain V11 Pro", "url": "/legacy",
     "desc": "The original monolith brain GUI, byte-identical through the proxy.",
     "health": "/api/state", "tag": "8787→8791"},
    {"id": "defense", "name": "DEFENSE LAYER — Knowledge Surface", "url": "/defense/knowledge-surface",
     "desc": "System-wide defense: quarantine, audit chains, kernel findings, self-heal with baseline + toggle.",
     "health": "/defense/api/health", "tag": "8792"},
    {"id": "neural", "name": "NEURAL STREAM — live everything", "url": "/neural-stream",
     "desc": "Merged live feed: beast messaging, process flows, monolith events, defense events — one stream.",
     "health": "/neural/feed", "tag": "portal"},
    {"id": "api", "name": "GODSCOPE API + MCP", "url": "/beast/api/godscope/endpoints",
     "desc": "98 CLI commands + 39 original + 18 beast endpoints, 60 MCP tools, /v1/messages Anthropic-style.",
     "health": "/beast/api/godscope/endpoints", "tag": "api"},
    {"id": "messages", "name": "MESSAGES — Kimi Code / Anthropic", "url": "/beast/#messages",
     "desc": "Anthropic-style /v1/messages + OpenClaw/Hermes .openclaw bridge calls.",
     "health": "/beast/api/messaging/status", "tag": "v1"},
    {"id": "a2a", "name": "A2A — talk to the agent (real protocol)", "url": "/a2a",
     "desc": "Agent card at /.well-known/agent.json, /a2a/message, peer registry, outbound send — the agent exists in the world and is bridgeable.",
     "health": "/.well-known/agent.json", "tag": "a2a"},
    {"id": "os", "name": "OS CONSOLE — real terminal", "url": "/os",
     "desc": "Full-scope exec, process hosting sessions, form-executable (builds the binary when none exists), file r/w, ps, 1300+ tools.",
     "health": "/beast/api/os/ps", "tag": "os"},
    {"id": "term", "name": "TERM HOST — run processes live", "url": "/term",
     "desc": "Start/host/kill long-running process sessions with live output.",
     "health": "/beast/api/os/term/list", "tag": "term"},
    {"id": "raw", "name": "FULL RAW — everything it scans", "url": "/raw",
     "desc": "Uncapped dumps, raw code any file, code-context around findings, all logs.",
     "health": "/portal/api/code-context", "tag": "raw"},
    {"id": "cheat", "name": "CHEAT ENGINE — kernel-level views", "url": "/cheat",
     "desc": "Process maps, raw memory reads, flashing watch cells, live node editing.",
     "health": "/beast/api/os/ps", "tag": "cheat"},
    {"id": "supervisor", "name": "HUMAN SUPERVISOR REVIEW — all exposed", "url": "/supervisor",
     "desc": "Everything visible: secrets, env, configs, logs, full raw code of every file.",
     "health": "/portal/api/supervisor/secrets", "tag": "review"},
]


def hub_html() -> str:
    health = _fetch_json(f"{BEAST_URL}/beast/api/health", 5) or _baked("beast_health.json") or {}
    cards = ""
    for s in SYSTEMS:
        cards += f"""
      <a class="card" href="{s['url']}">
        <div class="ctitle"><span class="dot" id="dot-{s['id']}"></span>{s['name']}<span class="tag">{s['tag']}</span></div>
        <div class="cdesc">{s['desc']}</div>
        <div class="cmeta" id="meta-{s['id']}">…</div>
      </a>"""
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>SSB Z-MARK COMPLETE — Portal</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{{--cyan:#42f8ff;--mag:#ff2bd6;--grn:#7dffca;--amb:#ffd447;--red:#ff1765;--dim:#7a8694;--bg:#020407;--panel:#05080c;--bd:#12343f}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:#d5f7ff;font-family:ui-monospace,Menlo,Consolas,monospace;min-height:100vh;
 background-image:radial-gradient(ellipse at 20% 0%,#06121a 0%,transparent 55%),linear-gradient(#020407,#020407)}}
.wrap{{max-width:1200px;margin:0 auto;padding:28px 18px 60px}}
h1{{font-size:22px;letter-spacing:4px;color:var(--cyan);text-shadow:0 0 18px #42f8ff55}}
.sub{{color:var(--dim);font-size:11px;letter-spacing:2px;margin:6px 0 22px}}
.stats{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:22px}}
.chip{{border:1px solid var(--bd);background:var(--panel);padding:6px 12px;font-size:11px;letter-spacing:1px}}
.chip b{{color:var(--grn)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:14px}}
.card{{display:block;border:1px solid var(--bd);background:linear-gradient(180deg,#06121a,#04070b);padding:16px;text-decoration:none;color:inherit;transition:.15s}}
.card:hover{{border-color:var(--cyan);box-shadow:0 0 24px #42f8ff22;transform:translateY(-2px)}}
.ctitle{{font-size:13px;letter-spacing:1.5px;color:var(--cyan);display:flex;align-items:center;gap:8px}}
.tag{{margin-left:auto;font-size:9px;color:var(--amb);border:1px solid #4a3c10;padding:1px 6px}}
.cdesc{{font-size:11px;color:#9fb3c0;margin:10px 0;line-height:1.5}}
.cmeta{{font-size:10px;color:var(--dim);letter-spacing:1px}}
.dot{{width:9px;height:9px;border-radius:50%;background:var(--dim);display:inline-block}}
.dot.up{{background:var(--grn);box-shadow:0 0 10px var(--grn)}}
.dot.down{{background:var(--red);box-shadow:0 0 10px var(--red)}}
.foot{{margin-top:26px;font-size:10px;color:var(--dim);letter-spacing:1px}}
a{{color:var(--cyan)}}
</style></head><body><div class="wrap">
<h1>SSB Z-MARK COMPLETE</h1>
<div class="sub">ONE URL · EVERY GUI · LIVE NEURAL STREAM · NO 404s</div>
<div class="stats" id="chips">
 <span class="chip">NODES <b>{health.get('nodes','—')}</b></span>
 <span class="chip">EDGES <b>{health.get('edges','—')}</b></span>
 <span class="chip">EVENTS <b>{health.get('events','—')}</b></span>
 <span class="chip">MEMORIES <b>{health.get('memories_loaded','—')}</b></span>
 <span class="chip">MESSAGING <b>{'LIVE' if health.get('messaging',{}).get('initialized') else '—'}</b></span>
</div>
<div class="grid">{cards}
</div>
<div class="foot">portal uptime <span id="up">0</span>s · beast {BEAST_URL} · defense {DEFENSE_URL} · snapshots in /data seed first paint, live polls refresh</div>
</div>
<script>
const t0=Date.now();setInterval(()=>{{document.getElementById('up').textContent=Math.floor((Date.now()-t0)/1000)}},1000);
async function tick(){{
 try{{const r=await fetch('/portal/api/status');const d=await r.json();
  for(const s of d.systems){{const dot=document.getElementById('dot-'+s.id);if(dot)dot.className='dot '+(s.up?'up':'down');
   const m=document.getElementById('meta-'+s.id);if(m&&s.meta)m.textContent=s.meta;}}
 }}catch(e){{}}
}}
setInterval(tick,4000);tick();
</script>
</body></html>"""


NEURAL_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>NEURAL STREAM — SSB Z-MARK</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--cyan:#42f8ff;--mag:#ff2bd6;--grn:#7dffca;--amb:#ffd447;--red:#ff1765;--dim:#7a8694;--bg:#020407;--bd:#12343f}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:#d5f7ff;font-family:ui-monospace,Menlo,Consolas,monospace}
header{padding:14px 18px;border-bottom:1px solid var(--bd);display:flex;gap:14px;align-items:center;position:sticky;top:0;background:#020407ee}
h1{font-size:15px;letter-spacing:3px;color:var(--cyan)}
.count{color:var(--dim);font-size:11px}
#stream{padding:10px 18px}
.row{padding:4px 8px;border-left:2px solid var(--bd);margin:3px 0;font-size:11px;animation:in .25s}
@keyframes in{from{opacity:0;transform:translateX(-6px)}to{opacity:1}}
.src{font-weight:700;margin-right:8px}
.src.beast{color:var(--cyan)}.src.monolith{color:var(--grn)}.src.defense{color:var(--amb)}.src.messaging{color:var(--mag)}
.lbl{color:#9fb3c0}.t{color:var(--dim);font-size:9px;margin-right:8px}
a{color:var(--cyan);font-size:11px}
.pause{color:var(--amb);cursor:pointer;font-size:10px;border:1px solid #4a3c10;padding:2px 8px}
</style></head><body>
<header><h1>NEURAL STREAM</h1><span class="count" id="c">0 events</span>
<span class="pause" id="pp">PAUSE</span><a href="/">← portal</a></header>
<div id="stream"></div>
<script>
const seen=new Set();let paused=false,count=0;
const streamEl=document.getElementById('stream');
document.getElementById('pp').onclick=()=>{paused=!paused;document.getElementById('pp').textContent=paused?'RESUME':'PAUSE'};
function add(src,ts,label){
 if(paused)return;
 const key=src+ts+label;if(seen.has(key))return;seen.add(key);
 if(seen.size>3000){seen.delete(seen.values().next().value)}
 const d=document.createElement('div');d.className='row';
 const t=ts?new Date(ts*1000).toLocaleTimeString():'--:--:--';
 d.innerHTML='<span class="t">'+t+'</span><span class="src '+src+'">'+src.toUpperCase()+'</span><span class="lbl">'+String(label).replace(/</g,'&lt;').slice(0,220)+'</span>';
 streamEl.insertBefore(d,streamEl.firstChild);
 while(streamEl.children.length>400)streamEl.removeChild(streamEl.lastChild);
 count++;document.getElementById('c').textContent=count+' events';
}
async function poll(){
 try{const r=await fetch('/neural/feed?limit=250');const d=await r.json();
  (d.events||[]).forEach(e=>add(e.source,e.ts,e.label));}catch(e){}
}
setInterval(poll,1500);poll();
</script></body></html>"""


class PortalHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet
        pass

    # -- helpers ------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, default=str).encode(), "application/json")

    def _backend_down(self, name: str, url: str):
        body = f"""<html><body style="background:#020407;color:#d5f7ff;font-family:monospace;padding:40px">
        <h2 style="color:#ffd447">{name} offline</h2>
        <p>backend {url} is not responding right now. <a style="color:#42f8ff" href="/">← portal</a></p>
        </body></html>""".encode()
        self._send(200, body)  # never a bare 404 on wired links

    def _proxy(self, base: str, upstream_path: str, rewrite_html_prefix: str | None = None):
        url = base + upstream_path
        try:
            body_in = None
            if self.command in ("POST", "PUT", "PATCH"):
                ln = int(self.headers.get("Content-Length", 0) or 0)
                body_in = self.rfile.read(ln) if ln else None
            req = urllib.request.Request(url, data=body_in, method=self.command)
            for k, v in self.headers.items():
                if k.lower() not in HOP_BY_HOP and k.lower() != "host":
                    req.add_header(k, v)
            with urllib.request.urlopen(req, timeout=60) as resp:
                status, headers, body = resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            status, headers, body = exc.code, dict(exc.headers or {}), exc.read()
        except Exception:
            return self._backend_down(base.split("//")[-1].split(":")[0], base)
        ctype = headers.get("Content-Type", headers.get("content-type", "application/octet-stream"))
        if rewrite_html_prefix and ("text/html" in ctype or "javascript" in ctype):
            text = body.decode("utf-8", errors="replace")
            text = text.replace("'/api/", f"'{rewrite_html_prefix}/api/") \
                       .replace('"/api/', f'"{rewrite_html_prefix}/api/') \
                       .replace("(/api/", f"({rewrite_html_prefix}/api/") \
                       .replace("href=\"/", f"href=\"{rewrite_html_prefix}/") \
                       .replace("src=\"/", f"src=\"{rewrite_html_prefix}/")
            body = text.encode()
        self.send_response(status)
        for k, v in headers.items():
            if k.lower() not in HOP_BY_HOP and k.lower() != "content-length":
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    # -- routes -------------------------------------------------------
    def _route(self):
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/" or path == "/index.html":
            return self._send(200, hub_html().encode())
        if path == "/neural-stream":
            return self._send(200, NEURAL_HTML.encode())
        if path == "/neural/feed":
            return self._neural_feed(parse_qs(parsed.query))
        if path == "/portal/api/status":
            return self._status()
        if path.startswith("/data/"):
            fp = DATA_DIR / path[len("/data/"):]
            if fp.is_file() and fp.suffix == ".json":
                return self._send(200, fp.read_bytes(), "application/json")
            return self._json({"error": "no such snapshot"}, 404)
        if path.startswith("/defense"):
            upstream = path[len("/defense"):] or "/"
            if parsed.query:
                upstream += "?" + parsed.query
            return self._proxy(DEFENSE_URL, upstream, rewrite_html_prefix="/defense")
        # ---------------- v16 pack routes ----------------
        if V16 is not None:
            q = parse_qs(parsed.query)
            q1 = {k: (v[0] if v else "") for k, v in q.items()}
            body = None
            if self.command in ("POST", "PUT", "PATCH"):
                ln = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(ln) if ln else b""
                try:
                    body = json.loads(raw.decode("utf-8", errors="replace")) if raw else {}
                except Exception:
                    body = {}
            if path == "/.well-known/agent.json":
                return self._json(V16.AGENT_CARD)
            if path == "/a2a/message":
                if self.command != "POST":
                    return self._json({"error": "POST required"}, 405)
                return self._json(V16.a2a_message(body or {}))
            if path == "/a2a/peers":
                if self.command == "POST":
                    return self._json(V16.peers_add(body or {}))
                return self._json(V16.peers_list())
            if path == "/a2a/send":
                return self._json(V16.a2a_send(body or q1))
            if path == "/portal/api/voice/chat":
                if self.command != "POST":
                    return self._json({"error": "POST required"}, 405)
                return self._json(V16.voice_chat(body or {}))
            if path == "/portal/api/supervisor/secrets":
                return self._json(V16.supervisor_secrets())
            if path == "/portal/api/code-context":
                return self._json(V16.code_context(q1.get("path", ""), q1.get("line", "1"),
                                                   int(q1.get("context", "40") or 40)))
            if path == "/portal/api/node/edit":
                return self._json(V16.node_edit(body or q1))
            if path == "/beast/api/os/exec":
                return self._json(V16.os_exec(body or q1))
            if path == "/beast/api/os/write":
                return self._json(V16.os_write((body or q1).get("path", ""), (body or q1).get("content", "")))
            if path == "/beast/api/os/read":
                return self._json(V16.os_read(q1.get("path", "")))
            if path == "/beast/api/os/ps":
                return self._json(V16.os_ps())
            if path == "/beast/api/os/tools":
                return self._json(V16.os_tools())
            if path == "/beast/api/os/term/start":
                return self._json(V16.term_start(body or q1))
            if path == "/beast/api/os/term/output":
                return self._json(V16.term_output(q1.get("session", "")))
            if path == "/beast/api/os/term/kill":
                return self._json(V16.term_kill(q1.get("session", "")))
            if path == "/beast/api/os/term/list":
                return self._json(V16.term_list())
            if path == "/beast/api/os/form":
                return self._json(V16.form_executable(body or q1))
            if path == "/beast/api/raw/brain":
                return self._json(V16.raw_brain())
            if path == "/beast/api/raw/scans":
                return self._json(V16.raw_scans())
            if path == "/beast/api/raw/logs":
                return self._json(V16.raw_logs())
            if path == "/beast/api/cheat/maps":
                return self._json(V16.proc_maps(q1.get("pid", "0")))
            if path == "/beast/api/cheat/mem":
                return self._json(V16.proc_mem_read(q1.get("pid", "0"), q1.get("addr", "0"),
                                                    q1.get("len", "256")))
            if path == "/kit.js":
                fp = DATA_DIR.parent / "static" / "kit.js"
                if fp.is_file():
                    return self._send(200, fp.read_bytes(), ctype="application/javascript")
                return self._send(404, b"// missing")
            if path in ("/voice", "/os", "/raw", "/cheat", "/term", "/supervisor", "/a2a"):
                fp = DATA_DIR.parent / "static" / (path.strip("/") + ".html")
                if fp.is_file():
                    return self._send(200, fp.read_bytes())
                return self._send(200, self._backend_down(path, ""))
        # everything else -> beast front (covers /beast/*, /legacy*, /v1/*, /api/*)
        return self._proxy(BEAST_URL, self.path)

    def _neural_feed(self, query):
        limit = min(500, int(query.get("limit", ["250"])[0] or 250))
        events = []
        d = _fetch_json(f"{BEAST_URL}/beast/api/messaging/feed?limit=100", 4)
        for f in (d or {}).get("flows", []):
            events.append({"source": "messaging", "ts": f.get("ts"),
                           "label": f"{f.get('path')} {f.get('model')} via {f.get('provider_used')} — {f.get('prompt_preview','')}"})
        d = _fetch_json(f"{BEAST_URL}/beast/api/process-flow?limit=150", 4)
        for f in (d or {}).get("flows", []):
            events.append({"source": "beast", "ts": f.get("ts"),
                           "label": f"{f.get('src')} → {f.get('dst')} [{f.get('kind')}] {f.get('label','')}"})
        d = _fetch_json(f"{DEFENSE_URL}/api/events", 4)
        if isinstance(d, dict):
            for f in (d.get("events") or d.get("items") or [])[:80]:
                events.append({"source": "defense", "ts": f.get("ts") or f.get("time"),
                               "label": f.get("label") or f.get("type") or json.dumps(f)[:160]})
        events.sort(key=lambda e: e.get("ts") or 0, reverse=True)
        return self._json({"ok": True, "events": events[:limit], "ts": time.time()})

    def _status(self):
        out = []
        for s in SYSTEMS:
            meta = ""
            up = False
            if s["id"] == "beast":
                d = _fetch_json(f"{BEAST_URL}/beast/api/health", 4)
                up = bool(d and d.get("ok"))
                if d:
                    meta = f"nodes {d.get('nodes')} · edges {d.get('edges')} · memories {d.get('memories_loaded')}"
            elif s["id"] == "legacy":
                d = _fetch_json(f"{BEAST_URL}/api/state", 4)
                up = d is not None
                if d:
                    meta = f"state nodes {len(d.get('nodes', []))}"
            elif s["id"] == "defense":
                d = _fetch_json(f"{DEFENSE_URL}/api/health", 4)
                up = d is not None
                if d:
                    meta = "defense api up"
            elif s["id"] == "neural":
                up = True
                meta = "streaming"
            elif s["id"] == "api":
                d = _fetch_json(f"{BEAST_URL}/beast/api/godscope/endpoints", 4)
                up = bool(d and d.get("ok"))
                if d:
                    meta = f"{len(d.get('endpoints', []))} endpoints · {d.get('cli_command_count', '?')} commands"
            elif s["id"] == "messages":
                d = _fetch_json(f"{BEAST_URL}/beast/api/messaging/status", 4)
                up = bool(d and d.get("ok"))
                if d:
                    meta = f"recent {d.get('recent_messages', 0)} calls"
            out.append({"id": s["id"], "up": up, "meta": meta})
        return self._json({"ok": True, "uptime": time.time() - STARTED, "systems": out})

    def do_GET(self):
        try:
            self._route()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            try:
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)
            except Exception:
                pass

    do_POST = do_GET
    do_PUT = do_GET
    do_DELETE = do_GET
    do_HEAD = do_GET


def main():
    srv = ThreadingHTTPServer((HOST, PORT), PortalHandler)
    print(f"[ssb-portal] hub: http://{HOST}:{PORT}/  beast={BEAST_URL} defense={DEFENSE_URL}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
