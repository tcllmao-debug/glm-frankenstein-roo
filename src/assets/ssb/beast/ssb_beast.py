#!/usr/bin/env python3
"""
SSB BEAST LAYER (M1 + M3) — additive fan-out wrapper around the SSB monolith.

Contract: /tmp/ssb/SPEC.md

M1:
  * MonolithLoader   — load the repaired code-only monolith (pyc, source fallback),
                       __file__ pinned at the original monolith, sys.modules cache.
  * FanOutDispatcher — every command fires normal + broad + kernel lanes
                       concurrently and merges a combo envelope.
                       SSB_BEAST_SOLO=1 disables fan-out.
  * CLI              — brain / combo / chain-all / godscope / twin + pass-through
                       of any original command with fan-out.
  * BeastFrontServer — public server; /beast/api/* served locally against the
                       SAME shared brain; every other path byte-transparently
                       reverse-proxied to the unmodified internal original brain.
M3:
  * run_chain_all        — every CLI command chained in one full-scope run.
  * run_twin_evolution   — parallel twin brains scan the same targets, diff,
                           merge union without duplicates into a combined build.

Stdlib only. ADDITIVE ONLY: the original monolith is never modified and the
original brain server runs unmodified on the internal port.
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import http.client
import http.server
import io
import json
import os
import queue
import shlex
import socket
import socketserver
import sys
import threading
import time
import traceback
import urllib.parse
import hashlib
import math
import uuid
from pathlib import Path

SSB_DIR = Path(os.environ.get("SSB_BEAST_HOME", "/tmp/ssb"))
ORIGINAL_MONOLITH = Path(os.environ.get("SSB_MONOLITH_ORIGINAL", str(SSB_DIR / "ssb_monolith.py")))
REPAIRED_MONOLITH = Path(os.environ.get("SSB_MONOLITH_REPAIRED", str(SSB_DIR / "monolith_clean_repaired.py")))
REPAIRED_PYC = Path(os.environ.get("SSB_MONOLITH_PYC", str(SSB_DIR / "monolith_clean_repaired.pyc")))
def resolve_file_pin() -> str:
    """Which path to pin as the loaded module's __file__.

    The bundle reader (`iter_embedded_bundle_chunks`) scans `__file__` for
    `#SSB64:` chunks. The original monolith in THIS environment is truncated
    mid-bundle-chunk at exactly 100 MiB, so pinning at the original makes
    every bundle-dependent command (health, payload, ...) die with
    `binascii.Error: Incorrect padding`. Pinning at the repaired code-only
    file instead yields the monolith's graceful "no embedded bundle" path
    (embedded:false) and all commands work. Override with
    SSB_MONOLITH_FILE_PIN=/tmp/ssb/ssb_monolith.py on a complete original.
    """
    env = os.environ.get("SSB_MONOLITH_FILE_PIN", "").strip()
    if env:
        return env
    try:
        if REPAIRED_MONOLITH.exists():
            return str(REPAIRED_MONOLITH)
    except OSError:
        pass
    return "/tmp/ssb/ssb_monolith.py"

BEAST_VERSION = "1.0.0-m1m3"
LANE_TIMEOUT = float(os.environ.get("SSB_BEAST_LANE_TIMEOUT", "90") or 90)
CHAIN_CMD_TIMEOUT = float(os.environ.get("SSB_BEAST_CHAIN_TIMEOUT", "20") or 20)
PROXY_TIMEOUT = float(os.environ.get("SSB_BEAST_PROXY_TIMEOUT", "60") or 60)
PROXY_SLOW_TIMEOUT = float(os.environ.get("SSB_BEAST_PROXY_SLOW_TIMEOUT", "180") or 180)

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "trailers", "transfer-encoding", "upgrade",
}
SLOW_PROXY_PREFIXES = (
    "/api/scan-", "/api/omni-", "/api/singularity-status", "/api/hive-status",
    "/api/internet-audit", "/api/god-eye", "/api/file-chains",
)


def _log(msg: str) -> None:
    print(f"[ssb-beast] {msg}", file=sys.stderr, flush=True)


def _json_safe(value, depth: int = 0):
    """Best-effort conversion of anything into JSON-serializable data."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if depth > 12:
        return repr(value)
    try:
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {str(_json_safe(k, depth + 1)): _json_safe(v, depth + 1) for k, v in list(value.items())[:20000]}
        if isinstance(value, (list, tuple, set, frozenset)):
            return [_json_safe(v, depth + 1) for v in list(value)[:20000]]
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            return _json_safe(to_dict(), depth + 1)
        if hasattr(value, "__dataclass_fields__"):
            return _json_safe({k: getattr(value, k, None) for k in value.__dataclass_fields__}, depth + 1)
        json.dumps(value)
        return value
    except Exception:
        try:
            return repr(value)
        except Exception:
            return "<unserializable>"


def _json_dumps(obj, **kw) -> str:
    return json.dumps(_json_safe(obj), ensure_ascii=False, default=str, **kw)


# ---------------------------------------------------------------------------
# M1: MonolithLoader
# ---------------------------------------------------------------------------
class MonolithLoader:
    """Load the repaired code-only SSB monolith exactly once, as a module."""

    _module = None
    _lock = threading.Lock()
    _load_info = {}

    @classmethod
    def _ensure_repaired_source(cls, verbose: bool = True) -> Path:
        """Create the repaired code-only monolith from the original if absent.

        The original is NEVER modified; we only read it. Code is everything
        before the '# __SUPER_SQUISH_BUNDLE_BEGIN__' marker (or the first
        '#SSB64:' bundle comment line).
        """
        if REPAIRED_MONOLITH.exists() and REPAIRED_MONOLITH.stat().st_size > 0:
            return REPAIRED_MONOLITH
        if verbose:
            _log(f"repaired monolith missing; extracting code-only copy from {ORIGINAL_MONOLITH}")
        marker = "# __SUPER_SQUISH_BUNDLE_BEGIN__"
        written = 0
        tmp_path = REPAIRED_MONOLITH.with_suffix(".py.tmp")
        with open(ORIGINAL_MONOLITH, "r", encoding="utf-8", errors="replace") as src, \
                open(tmp_path, "w", encoding="utf-8") as dst:
            for line in src:
                if line.startswith(marker) or line.startswith("#SSB64:"):
                    break
                dst.write(line)
                written += 1
        tmp_path.replace(REPAIRED_MONOLITH)
        if verbose:
            _log(f"repaired monolith written: {REPAIRED_MONOLITH} ({written} code lines)")
        return REPAIRED_MONOLITH

    @classmethod
    def _ensure_pyc(cls, verbose: bool = True) -> Path:
        src = cls._ensure_repaired_source(verbose=verbose)
        rebuild = True
        try:
            if REPAIRED_PYC.exists() and REPAIRED_PYC.stat().st_size > 0:
                rebuild = REPAIRED_PYC.stat().st_mtime < src.stat().st_mtime
        except OSError:
            rebuild = True
        if rebuild:
            if verbose:
                _log(f"compiling {src} -> {REPAIRED_PYC} (one-time cache build)")
            import py_compile
            t0 = time.time()
            py_compile.compile(str(src), cfile=str(REPAIRED_PYC), doraise=True)
            if verbose:
                _log(f"pyc built in {time.time() - t0:.1f}s")
        return REPAIRED_PYC

    @classmethod
    def load(cls, verbose: bool = True):
        """Load and cache the monolith module. Never runs the monolith main()."""
        with cls._lock:
            cached = sys.modules.get("ssb_monolith")
            if cached is not None and hasattr(cached, "SSBGalaxyBrain"):
                cls._module = cached
                return cached
            # Embedded mode: inside the full patched monolith, the module is
            # already running as __main__ — use it directly (self-contained).
            if os.environ.get("SSB_BEAST_EMBEDDED") == "1":
                embedded = sys.modules.get("__main__")
                if embedded is not None and hasattr(embedded, "SSBGalaxyBrain"):
                    cls._module = embedded
                    cls._load_info = {"mode": "embedded", "file": getattr(embedded, "__file__", "")}
                    if verbose:
                        _log("monolith: embedded mode (patched monolith, self-contained)")
                    return embedded
            if cls._module is not None:
                return cls._module
            import importlib.machinery
            import importlib.util

            t0 = time.time()
            errors = []
            module = None
            # 1) Fast path: precompiled bytecode of the repaired monolith.
            try:
                pyc = cls._ensure_pyc(verbose=verbose)
                if verbose:
                    _log(f"loading monolith bytecode {pyc} ...")
                loader = importlib.machinery.SourcelessFileLoader("ssb_monolith", str(pyc))
                spec = importlib.util.spec_from_loader("ssb_monolith", loader)
                module = importlib.util.module_from_spec(spec)
                sys.modules["ssb_monolith"] = module
                loader.exec_module(module)
                cls._load_info = {"mode": "pyc", "path": str(pyc)}
            except Exception as exc:  # stale/corrupt pyc -> rebuild from source
                errors.append(f"pyc load failed: {exc}")
                sys.modules.pop("ssb_monolith", None)
                module = None
                try:
                    REPAIRED_PYC.unlink(missing_ok=True)
                except OSError:
                    pass
            # 2) Source fallback: compile repaired source in-process.
            if module is None:
                try:
                    src = cls._ensure_repaired_source(verbose=verbose)
                    if verbose:
                        _log(f"loading monolith source {src} ...")
                    spec = importlib.util.spec_from_file_location("ssb_monolith", str(src))
                    module = importlib.util.module_from_spec(spec)
                    sys.modules["ssb_monolith"] = module
                    spec.loader.exec_module(module)
                    cls._load_info = {"mode": "source", "path": str(src)}
                except Exception as exc:
                    errors.append(f"source load failed: {exc}")
                    sys.modules.pop("ssb_monolith", None)
                    module = None
            if module is None:
                raise RuntimeError("unable to load SSB monolith: " + " | ".join(errors))
            # Pin __file__ (see resolve_file_pin): default = repaired code-only
            # monolith so the truncated original's bundle can't crash commands.
            try:
                module.__file__ = resolve_file_pin()
            except Exception:
                pass
            cls._module = module
            cls._load_info["seconds"] = round(time.time() - t0, 2)
            cls._load_info["file_pin"] = getattr(module, "__file__", "")
            if verbose:
                _log(f"monolith loaded in {time.time() - t0:.1f}s via {cls._load_info.get('mode')}; "
                     f"__file__={module.__file__}")
            return module

    @classmethod
    def info(cls) -> dict:
        return dict(cls._load_info)


# ---------------------------------------------------------------------------
# M1: FanOutDispatcher — normal + broad + kernel fired concurrently
# ---------------------------------------------------------------------------
class FanOutDispatcher:
    """Fire every command as normal+broad+kernel concurrently; merge envelope."""

    BROAD_BOOL_ATTRS = (
        "full", "full_dump", "deep", "all", "explain_everything", "maximize",
        "aggressive", "complete", "recursive", "enumerate_all",
    )
    KERNEL_VALUE_ATTRS = ("focus", "mode", "scope", "layer", "surface", "profile")
    KERNEL_BOOL_ATTRS = ("kernel", "deep", "full")

    def __init__(self, brain=None, lane_timeout: float = LANE_TIMEOUT, solo: bool | None = None):
        self.brain = brain
        self.lane_timeout = float(lane_timeout or LANE_TIMEOUT)
        if solo is None:
            solo = os.environ.get("SSB_BEAST_SOLO", "") == "1"
        self.solo = bool(solo)
        self.stats = collections.Counter()
        self.lock = threading.Lock()

    # -- namespace mutations -------------------------------------------------
    @staticmethod
    def _mutate_broad(args: argparse.Namespace) -> list:
        changes = []
        for attr in FanOutDispatcher.BROAD_BOOL_ATTRS:
            if hasattr(args, attr):
                try:
                    setattr(args, attr, True)
                    changes.append(f"{attr}=True")
                except Exception:
                    pass
        for attr in ("limit", "max_files", "max_events", "max_watch_files", "depth"):
            if hasattr(args, attr):
                try:
                    cur = int(getattr(args, attr) or 0)
                    broadened = max(cur * 20, 100000)
                    setattr(args, attr, broadened)
                    changes.append(f"{attr}={broadened}")
                except Exception:
                    pass
        if hasattr(args, "format"):
            try:
                setattr(args, "format", "json")
                changes.append("format=json")
            except Exception:
                pass
        if hasattr(args, "json"):
            try:
                setattr(args, "json", True)
            except Exception:
                pass
        return changes

    @staticmethod
    def _mutate_kernel(args: argparse.Namespace) -> list:
        changes = []
        for attr in FanOutDispatcher.KERNEL_VALUE_ATTRS:
            if hasattr(args, attr):
                try:
                    setattr(args, attr, "kernel")
                    changes.append(f"{attr}=kernel")
                except Exception:
                    pass
        for attr in FanOutDispatcher.KERNEL_BOOL_ATTRS:
            if hasattr(args, attr):
                try:
                    setattr(args, attr, True)
                    changes.append(f"{attr}=True")
                except Exception:
                    pass
        return changes

    # -- lane execution ------------------------------------------------------
    def _run_command_lane(self, label: str, argv: list, mutate) -> dict:
        t0 = time.time()
        lane = {"lane": label, "ok": False, "argv": list(argv)}
        try:
            module = MonolithLoader.load(verbose=False)
            parser = module.build_parser()
            norm = getattr(module, "normalize_global_args", None)
            argv2 = norm(list(argv)) if callable(norm) else list(argv)
            args = parser.parse_args(argv2)
            if mutate is not None:
                lane["mutations"] = mutate(args)
            result = module.run_command(args)
            lane["ok"] = bool(getattr(result, "success", False))
            lane["exit_code"] = int(getattr(result, "exit_code", 0) or 0)
            lane["message"] = str(getattr(result, "message", "") or "")
            lane["data"] = _json_safe(getattr(result, "data", None))
        except SystemExit as exc:
            lane["error"] = f"argparse exit: {exc.code}"
        except Exception as exc:
            lane["error"] = f"{type(exc).__name__}: {exc}"
            lane["trace"] = traceback.format_exc()[-2000:]
        lane["duration_s"] = round(time.time() - t0, 3)
        return lane

    def _run_kernel_lane(self, argv: list) -> dict:
        lane = self._run_command_lane("kernel", argv, self._mutate_kernel)
        # Kernel co-equal: fire the kernel-surface scan alongside the command.
        if self.brain is not None:
            t0 = time.time()
            try:
                lane["kernel_surface"] = _json_safe(self.brain.scan_kernel_surface())
            except Exception as exc:
                lane["kernel_surface_error"] = f"{type(exc).__name__}: {exc}"
            lane["kernel_surface_duration_s"] = round(time.time() - t0, 3)
        return lane

    def _parallel(self, tasks: dict, timeout: float) -> dict:
        """Run {label: fn} concurrently in daemon threads; tolerate timeouts."""
        results = {}
        q: "queue.Queue" = queue.Queue()

        def _wrap(lbl, fn):
            try:
                q.put((lbl, fn()))
            except Exception as exc:
                q.put((lbl, {"lane": lbl, "ok": False,
                             "error": f"{type(exc).__name__}: {exc}",
                             "trace": traceback.format_exc()[-2000:]}))

        for label, fn in tasks.items():
            threading.Thread(target=_wrap, args=(label, fn),
                             name=f"beast-lane-{label}", daemon=True).start()
        deadline = time.time() + max(1.0, float(timeout))
        while len(results) < len(tasks):
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                label, res = q.get(timeout=min(0.5, remaining))
                results[label] = res
            except queue.Empty:
                pass
        for label in tasks:
            if label not in results:
                results[label] = {"lane": label, "ok": False,
                                  "error": f"timeout after {timeout}s", "timeout": True}
        return results

    # -- public API ----------------------------------------------------------
    def fanout(self, argv: list, timeout: float | None = None) -> dict:
        """Fan one command out to normal+broad+kernel and merge the envelope."""
        argv = [str(a) for a in argv if str(a).strip()]
        if not argv:
            return {"ok": False, "error": "empty command argv", "mode": "combo"}
        timeout = float(timeout or self.lane_timeout)
        t0 = time.time()
        command = argv[0]
        if self.solo:
            lanes = {"normal": self._run_command_lane("normal", argv, None)}
            mode = "solo"
        else:
            lanes = self._parallel({
                "normal": lambda: self._run_command_lane("normal", argv, None),
                "broad": lambda: self._run_command_lane("broad", argv, self._mutate_broad),
                "kernel": lambda: self._run_kernel_lane(argv),
            }, timeout=timeout)
            mode = "combo"
        errors = {k: v.get("error") for k, v in lanes.items() if isinstance(v, dict) and v.get("error")}
        successes = [k for k, v in lanes.items() if isinstance(v, dict) and v.get("ok")]
        merged = {
            "ok": bool(successes),
            "lanes_ok": successes,
            "lanes_failed": sorted(set(lanes) - set(successes)),
            "errors": errors,
            "combined_data": {k: v.get("data") for k, v in lanes.items()
                              if isinstance(v, dict) and v.get("data") is not None},
        }
        envelope = {
            "ok": merged["ok"],
            "engine": "SSB-BEAST",
            "beast_version": BEAST_VERSION,
            "mode": mode,
            "command": command,
            "argv": argv,
            "lanes": lanes,
            "merged": merged,
            "lane_timeout_s": timeout,
            "duration_s": round(time.time() - t0, 3),
            "ts": time.time(),
        }
        with self.lock:
            self.stats["fanout"] += 1
            self.stats[f"cmd:{command}"] += 1
        return envelope

    def run_solo_original(self, argv: list) -> int:
        """SSB_BEAST_SOLO=1 path: behave exactly like the original CLI."""
        module = MonolithLoader.load()
        parser = module.build_parser()
        norm = getattr(module, "normalize_global_args", None)
        argv2 = norm(list(argv)) if callable(norm) else list(argv)
        args = parser.parse_args(argv2)
        result = module.run_command(args)
        fmt = getattr(args, "format", "json")
        try:
            rendered = module.format_result(result, fmt)
        except Exception:
            rendered = _json_dumps(getattr(result, "data", result), indent=2)
        output = getattr(args, "output", "")
        if output and not module.command_consumes_output(args):
            Path(output).expanduser().write_text(rendered, encoding="utf-8")
        else:
            print(rendered)
        return int(getattr(result, "exit_code", 0) or 0)


# ---------------------------------------------------------------------------
# M3: run_chain_all — every command chained in one full-scope run
# ---------------------------------------------------------------------------
# Commands that block, read stdin, fork daemons, patch files, or build huge
# bundles are skipped by default (override with include= / skip=).
CHAIN_DEFAULT_SKIP = {
    "brain", "serve", "mcp", "connect-mcp", "daemon", "start-daemon",
    "stop-daemon", "chat", "bundle", "self-improve", "vfix-v2", "sandbox",
    "ai-socket", "screenshot", "screenshot-analysis", "bridge", "configure-ai",
    "god", "beast", "ultra", "decrypt-audit", "god-eye",
}


def list_cli_commands(module=None) -> list:
    module = module or MonolithLoader.load(verbose=False)
    parser = module.build_parser()
    for action in parser._actions:
        if action.__class__.__name__ == "_SubParsersAction":
            return sorted(action.choices.keys())
    return []


def _subparser_for(module, command: str):
    parser = module.build_parser()
    for action in parser._actions:
        if action.__class__.__name__ == "_SubParsersAction":
            return action.choices.get(command)
    return None


def _first_workspace_file(workspace: str) -> str:
    try:
        root = Path(workspace).expanduser()
        if root.is_file():
            return str(root)
        for current, dir_names, file_names in os.walk(root):
            dir_names[:] = [d for d in dir_names if not d.startswith(".")][:20]
            for name in sorted(file_names)[:50]:
                candidate = Path(current) / name
                try:
                    if candidate.is_file() and candidate.stat().st_size < 2_000_000:
                        return str(candidate)
                except OSError:
                    continue
    except Exception:
        pass
    return ""


def synthesize_argv(module, command: str, workspace: str = ".") -> list:
    """Build the safest plausible argv for a command by inspecting its parser."""
    argv = [command]
    sub = _subparser_for(module, command)
    if sub is None:
        return argv
    try:
        option_strings = set()
        positionals = []
        required_options = []
        for act in sub._actions:
            if act.option_strings:
                option_strings.update(act.option_strings)
                if getattr(act, "required", False):
                    required_options.append(act)
            elif act.dest not in ("help",) and act.nargs in ("?", "*"):
                positionals.append(act.dest)
        sample_file = ""
        # Required options must be satisfied or argparse exits 2.
        for act in required_options:
            flag = next((s for s in act.option_strings if s.startswith("--")), act.option_strings[0])
            if act.dest in ("file", "input", "path") or "file" in act.dest:
                sample_file = sample_file or _first_workspace_file(workspace)
                value = sample_file or workspace
            elif getattr(act, "type", None) is int:
                value = "1"
            else:
                value = "chain-all-probe"
            argv += [flag, value]
        if "--workspace" in option_strings:
            argv += ["--workspace", workspace]
        elif "--target" in option_strings:
            argv += ["--target", workspace]
        elif "--input" in option_strings:
            argv += ["--input", workspace]
        elif "--file" in option_strings and "--file" not in argv:
            sample_file = sample_file or _first_workspace_file(workspace)
            if sample_file:
                argv += ["--file", sample_file]
        elif positionals and positionals[0] in ("target", "path", "input", "root", "workspace"):
            argv.append(workspace)
    except Exception:
        pass
    return argv


def _chain_command_subprocess(command: str, argv: list, workspace: str, timeout: float):
    """Run one chain command in a memory-isolated subprocess (solo mode)."""
    import subprocess
    t0 = time.time()
    env = dict(os.environ)
    env["SSB_BEAST_SOLO"] = "1"
    cmd = [sys.executable, os.path.abspath(__file__)] + [str(a) for a in argv]
    if "--workspace" not in [str(a) for a in argv]:
        cmd += ["--workspace", str(workspace)]
    hard_timeout = max(float(timeout), 20.0) + 15.0  # allow for monolith boot
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=hard_timeout, env=env)
        out = (proc.stdout or b"").decode("utf-8", errors="replace")
        ok = proc.returncode == 0
        try:
            start = out.find("{")
            parsed = json.loads(out[start:]) if start >= 0 else {}
            if isinstance(parsed, dict) and parsed.get("ok") is False:
                ok = False
        except Exception:
            parsed = {}
        entry = {"command": command, "argv": argv, "ok": ok, "mode": "subprocess-solo",
                 "duration_s": round(time.time() - t0, 2), "exit_code": proc.returncode,
                 "stdout_bytes": len(out)}
        if not ok:
            entry["error"] = (out[-300:] or (proc.stderr or b"").decode("utf-8", errors="replace")[-300:])
        return ok, entry
    except subprocess.TimeoutExpired:
        return False, {"command": command, "argv": argv, "ok": False, "mode": "subprocess-solo",
                       "duration_s": round(time.time() - t0, 2), "error": f"timeout after {hard_timeout}s (killed)"}
    except Exception as exc:
        return False, {"command": command, "argv": argv, "ok": False,
                       "error": f"{type(exc).__name__}: {exc}"}


def run_chain_all(workspace: str = ".", timeout: float = CHAIN_CMD_TIMEOUT,
                  include: list | None = None, skip: list | None = None,
                  only: list | None = None, budget_s: float = 0,
                  dispatcher: FanOutDispatcher | None = None,
                  brain=None, log=_log) -> dict:
    """Chain every CLI command in one run as a full-scope scanner."""
    t0 = time.time()
    module = MonolithLoader.load(verbose=False)
    dispatcher = dispatcher or FanOutDispatcher(brain=brain)
    commands = list_cli_commands(module)
    skip_set = set(CHAIN_DEFAULT_SKIP) | set(skip or [])
    skip_set -= set(include or [])
    if only:
        wanted = set(only)
        chain = [c for c in commands if c in wanted]
        skipped = sorted(set(commands) - set(chain))
    else:
        chain = [c for c in commands if c not in skip_set]
        skipped = sorted(set(commands) & skip_set)
    results = []
    succeeded = failed = 0
    for index, command in enumerate(chain, 1):
        if budget_s and (time.time() - t0) > budget_s:
            for rest in chain[index - 1:]:
                results.append({"command": rest, "ok": False, "skipped_budget": True})
            log(f"chain-all budget {budget_s}s exhausted at command {index}/{len(chain)}")
            break
        argv = synthesize_argv(module, command, workspace)
        log(f"chain-all [{index}/{len(chain)}] firing: {' '.join(argv)}")
        if os.environ.get("SSB_BEAST_CHAIN_SUBPROCESS", "1") != "0":
            # Memory-isolated subprocess mode (default): each command runs solo
            # in its own process; timeout hard-kills it; RAM fully reclaimed.
            ok, entry = _chain_command_subprocess(command, argv, workspace, timeout)
        else:
            try:
                envelope = dispatcher.fanout(argv, timeout=timeout)
                ok = bool(envelope.get("ok"))
                entry = {
                    "command": command, "argv": argv, "ok": ok,
                    "mode": envelope.get("mode"),
                    "duration_s": envelope.get("duration_s"),
                    "lanes_ok": envelope.get("merged", {}).get("lanes_ok", []),
                    "errors": envelope.get("merged", {}).get("errors", {}),
                }
            except Exception as exc:
                ok = False
                entry = {"command": command, "argv": argv, "ok": False,
                         "error": f"{type(exc).__name__}: {exc}"}
        succeeded += 1 if ok else 0
        failed += 0 if ok else 1
        results.append(entry)
        if brain is not None:
            try:
                brain.process_record({"kind": "command", "event": "chain_all",
                                      "command": command,
                                      "payload": {"ok": ok, "argv": argv}},
                                     broadcast=False)
            except Exception:
                pass
    report = {
        "ok": failed == 0,
        "engine": "SSB-BEAST",
        "mode": "chain-all",
        "workspace": workspace,
        "total": len(results),
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped,
        "timeout_s_per_command": timeout,
        "duration_s": round(time.time() - t0, 3),
        "results": results,
        "ts": time.time(),
    }
    return report


# ---------------------------------------------------------------------------
# M3: twin evolution — parallel twin against same targets, diff, merge union
# ---------------------------------------------------------------------------
def _brain_ids(brain) -> tuple:
    try:
        with brain.lock:
            return set(brain.nodes.keys()), set(brain.edges.keys())
    except Exception:
        return set(brain.nodes.keys()), set(brain.edges.keys())


def _merge_brain_union(src, dst) -> dict:
    """Merge src nodes/edges into dst without duplicates. Returns stats."""
    stats = {"nodes_added": 0, "edges_added": 0, "duplicates_avoided": 0}
    try:
        with src.lock:
            src_nodes = list(src.nodes.values())
            src_edges = list(src.edges.values())
        dst_node_ids, dst_edge_ids = _brain_ids(dst)
        for node in src_nodes:
            nid = node.get("id")
            if not nid:
                continue
            if nid in dst_node_ids:
                stats["duplicates_avoided"] += 1
                continue
            data = dict(node.get("data") or {})
            for key in ("severity", "alive", "online", "active_until", "wiki",
                        "raw_preview", "size", "path"):
                if key in node:
                    data[key] = node[key]
            dst.add_node(nid, node.get("label", nid), node.get("kind", "unknown"), data)
            stats["nodes_added"] += 1
        for edge in src_edges:
            eid = edge.get("id")
            if not eid:
                continue
            if eid in dst_edge_ids:
                stats["duplicates_avoided"] += 1
                continue
            dst.add_edge(edge.get("source", ""), edge.get("target", ""),
                         edge.get("kind", "linked"),
                         strength=float(edge.get("strength", 1.0) or 1.0),
                         data=dict(edge.get("data") or {}))
            stats["edges_added"] += 1
    except Exception as exc:
        stats["error"] = f"{type(exc).__name__}: {exc}"
    return stats


def _guarded_scan(fn, timeout: float = 60.0) -> dict:
    """Run a scan in a daemon thread with a timeout; never raise."""
    q: "queue.Queue" = queue.Queue()

    def _wrap():
        try:
            q.put(("ok", fn()))
        except Exception as exc:
            q.put(("error", f"{type(exc).__name__}: {exc}"))

    threading.Thread(target=_wrap, name="beast-scan", daemon=True).start()
    try:
        status, payload = q.get(timeout=max(1.0, float(timeout)))
        return {"status": status, "payload": _json_safe(payload)}
    except queue.Empty:
        return {"status": "timeout", "payload": None}


def run_twin_evolution(rounds: int = 2, workspace: str = ".", target: str = "",
                       merge_out: str = "", scan_timeout: float = 60.0,
                       max_files: int = 2000, log=_log) -> dict:
    """Clone the tool against itself: two twin brains scan the same targets,
    diff outputs each round, merge union without duplicates into a combined build."""
    t0 = time.time()
    module = MonolithLoader.load(verbose=False)
    workspace = str(Path(workspace).expanduser())
    target = target or workspace
    mk = lambda name: module.SSBGalaxyBrain(  # noqa: E731
        roots=[workspace, target], max_events=5000, poll_interval=1.0,
        max_watch_files=max(1000, max_files))
    alpha = mk("alpha")
    beta = mk("beta")
    for b, tag in ((alpha, "alpha"), (beta, "beta")):
        try:
            b.add_node(f"twin:{tag}", f"Twin {tag}", "brain",
                       {"severity": "info", "wiki": f"BEAST twin-evolution instance {tag}"})
        except Exception:
            pass
    report = {"ok": True, "engine": "SSB-BEAST", "mode": "twin-evolution",
              "rounds": int(rounds), "workspace": workspace, "target": target,
              "rounds_detail": [], "ts": time.time()}
    combined = mk("combined")
    for rnd in range(1, int(rounds) + 1):
        log(f"twin-evolution round {rnd}/{rounds}: scanning {target!r} with both twins")
        round_info = {"round": rnd}

        def _twin_scan(brain):
            # scan_workspace exists only on some monolith builds; the watch-root
            # scan is the always-present full-scope walker over watched roots.
            scan_ws = getattr(brain, "scan_workspace", None)
            if callable(scan_ws):
                try:
                    return scan_ws(Path(target), max_files=max_files)
                except TypeError:
                    pass
            brain.scan_watch_roots_once()
            return {"nodes": len(brain.nodes), "edges": len(brain.edges)}

        scans = {
            "alpha": _guarded_scan(lambda: _twin_scan(alpha), timeout=scan_timeout),
            "beta": _guarded_scan(lambda: _twin_scan(beta), timeout=scan_timeout),
        }
        a_nodes, a_edges = _brain_ids(alpha)
        b_nodes, b_edges = _brain_ids(beta)
        round_info["alpha"] = {"nodes": len(a_nodes), "edges": len(a_edges),
                               "scan": scans["alpha"]["status"]}
        round_info["beta"] = {"nodes": len(b_nodes), "edges": len(b_edges),
                              "scan": scans["beta"]["status"]}
        round_info["diff"] = {
            "alpha_only_nodes": len(a_nodes - b_nodes),
            "beta_only_nodes": len(b_nodes - a_nodes),
            "alpha_only_edges": len(a_edges - b_edges),
            "beta_only_edges": len(b_edges - a_edges),
        }
        # Cross-pollinate: merge each twin's unique findings into the other.
        m_ab = _merge_brain_union(beta, alpha)
        m_ba = _merge_brain_union(alpha, beta)
        round_info["cross_merge"] = {"beta_into_alpha": m_ab, "alpha_into_beta": m_ba}
        report["rounds_detail"].append(round_info)
        log(f"twin-evolution round {rnd}: alpha={len(a_nodes)}n/{len(a_edges)}e "
            f"beta={len(b_nodes)}n/{len(b_edges)}e diff={round_info['diff']}")
    # Final: merge union of both twins into the combined build, no duplicates.
    m1 = _merge_brain_union(alpha, combined)
    m2 = _merge_brain_union(beta, combined)
    c_nodes, c_edges = _brain_ids(combined)
    report["merged_nodes"] = len(c_nodes)
    report["merged_edges"] = len(c_edges)
    report["duplicates_avoided"] = int(m1.get("duplicates_avoided", 0) + m2.get("duplicates_avoided", 0))
    report["merge_stats"] = {"alpha_into_combined": m1, "beta_into_combined": m2}
    report["duration_s"] = round(time.time() - t0, 3)
    combined_build = {
        "engine": "SSB-BEAST",
        "kind": "twin-evolution-combined-build",
        "ts": time.time(),
        "workspace": workspace, "target": target, "rounds": int(rounds),
        "nodes": [ _json_safe(n) for n in combined.nodes.values() ],
        "edges": [ _json_safe(e) for e in combined.edges.values() ],
        "report": {k: v for k, v in report.items() if k != "rounds_detail"},
    }
    if not merge_out:
        merge_out = str(SSB_DIR / "project" / f"twin_evolution_merged_{int(time.time())}.json")
    try:
        Path(merge_out).parent.mkdir(parents=True, exist_ok=True)
        Path(merge_out).write_text(_json_dumps(combined_build, indent=2), encoding="utf-8")
        report["merged_build_path"] = merge_out
    except Exception as exc:
        report["ok"] = False
        report["merge_write_error"] = f"{type(exc).__name__}: {exc}"
    return report


# ---------------------------------------------------------------------------
# M1: shared core (one brain instance shared by internal server + Beast API)
# ---------------------------------------------------------------------------
class BeastCore:
    def __init__(self, module, brain, host: str, port: int,
                 internal_host: str, internal_port: int, workspace: str):
        self.module = module
        self.brain = brain
        self.host = host
        self.port = int(port)
        self.internal_host = internal_host
        self.internal_port = int(internal_port)
        self.workspace = workspace
        self.started_at = time.time()
        self.dispatcher = FanOutDispatcher(brain=brain)
        self.jobs: dict[str, dict] = {}
        self.jobs_lock = threading.Lock()
        self.job_counter = 0

    def start_job(self, kind: str, fn) -> str:
        with self.jobs_lock:
            self.job_counter += 1
            job_id = f"{kind}-{int(self.started_at)}-{self.job_counter}"
            self.jobs[job_id] = {"id": job_id, "kind": kind, "status": "running",
                                 "started": time.time(), "result": None, "error": ""}

        def _run():
            try:
                result = fn()
                with self.jobs_lock:
                    self.jobs[job_id]["status"] = "done"
                    self.jobs[job_id]["result"] = _json_safe(result)
            except Exception as exc:
                with self.jobs_lock:
                    self.jobs[job_id]["status"] = "error"
                    self.jobs[job_id]["error"] = f"{type(exc).__name__}: {exc}"
                    self.jobs[job_id]["result"] = {"trace": traceback.format_exc()[-3000:]}
            finally:
                with self.jobs_lock:
                    self.jobs[job_id]["finished"] = time.time()

        threading.Thread(target=_run, name=f"beast-job-{job_id}", daemon=True).start()
        return job_id

    def job_status(self, job_id: str) -> dict:
        with self.jobs_lock:
            job = self.jobs.get(job_id)
            if not job:
                return {"ok": False, "error": f"unknown job {job_id}"}
            out = dict(job)
            out["ok"] = job["status"] != "error"
            return _json_safe(out)


# ---------------------------------------------------------------------------
# Original monolith /api/* surface (served UNMODIFIED on the internal port;
# Beast proxies these byte-transparently and exposes them via GodScope/MCP).
# ---------------------------------------------------------------------------
ORIGINAL_API_ROUTES = [
    {"name": "root_gui", "path": "/", "params": [], "description": "Original Galaxy Brain HTML GUI"},
    {"name": "state", "path": "/api/state", "params": [{"name": "limit", "default": "5000"}], "description": "Compact brain state (nodes/edges/events)"},
    {"name": "node", "path": "/api/node", "params": [{"name": "id", "default": ""}, {"name": "path", "default": ""}], "description": "Full detail for one node"},
    {"name": "wiki", "path": "/api/wiki", "params": [{"name": "q", "default": ""}], "description": "Wiki search across nodes"},
    {"name": "directory", "path": "/api/directory", "params": [{"name": "root", "default": ""}, {"name": "limit", "default": "8000"}], "description": "Directory tree map"},
    {"name": "raw", "path": "/api/raw", "params": [{"name": "id", "default": ""}, {"name": "path", "default": ""}, {"name": "offset", "default": "0"}, {"name": "size", "default": "65536"}], "description": "Raw chunk of a file node"},
    {"name": "god_eye", "path": "/api/god-eye", "params": [{"name": "target", "default": ""}, {"name": "id", "default": ""}, {"name": "limit", "default": "2000"}], "description": "Focused God Eye context bundle"},
    {"name": "permissions", "path": "/api/permissions", "params": [{"name": "path", "default": ""}], "description": "Permission map for a path"},
    {"name": "health_lines", "path": "/api/health-lines", "params": [{"name": "root", "default": ""}], "description": "Health lines for a root"},
    {"name": "scan_keys", "path": "/api/scan-keys", "params": [], "description": "Secret/key finding nodes"},
    {"name": "god_omni_status", "path": "/api/god-omni-status", "params": [], "description": "God Omni core status"},
    {"name": "omni_enum", "path": "/api/omni-enum", "params": [], "description": "Maximized enumeration"},
    {"name": "omni_find", "path": "/api/omni-find", "params": [{"name": "q", "default": ""}], "description": "Deep info finder"},
    {"name": "neural_state", "path": "/api/neural-state", "params": [], "description": "Live neural state"},
    {"name": "nexus_vault", "path": "/api/nexus-vault", "params": [], "description": "Vault nodes"},
    {"name": "nexus_wedge", "path": "/api/nexus-wedge", "params": [{"name": "pid", "default": "0"}], "description": "Wedge a process"},
    {"name": "nexus_breach", "path": "/api/nexus-breach", "params": [{"name": "pid", "default": "0"}], "description": "Sandbox breach"},
    {"name": "singularity_status", "path": "/api/singularity-status", "params": [], "description": "Singularity status"},
    {"name": "hive_status", "path": "/api/hive-status", "params": [], "description": "Hive status"},
    {"name": "puppet_edit", "path": "/api/puppet-edit", "params": [{"name": "target", "default": ""}, {"name": "prompt", "default": ""}], "description": "Puppet master edit"},
    {"name": "value_scan", "path": "/api/value-scan", "params": [{"name": "pid", "default": "0"}, {"name": "pattern", "default": ""}, {"name": "type", "default": "int"}], "description": "Scan live process memory values"},
    {"name": "value_mod", "path": "/api/value-mod", "params": [{"name": "pid", "default": "0"}, {"name": "addr", "default": ""}, {"name": "val", "default": ""}], "description": "Modify live process memory value"},
    {"name": "kernel_scan", "path": "/api/kernel-scan", "params": [], "description": "Kernel surface scan"},
    {"name": "internet_audit", "path": "/api/internet-audit", "params": [], "description": "Internet/socket audit"},
    {"name": "process_connections", "path": "/api/process-connections", "params": [], "description": "Process and connection nodes"},
    {"name": "category", "path": "/api/category", "params": [{"name": "kind", "default": ""}], "description": "Nodes by kind"},
    {"name": "boot", "path": "/api/boot", "params": [], "description": "Write boot files"},
    {"name": "roots", "path": "/api/roots", "params": [], "description": "Watched roots + event log"},
    {"name": "omni_discovery", "path": "/api/omni-discovery", "params": [], "description": "Run all discovery scans in sequence"},
    {"name": "scan_watch_roots", "path": "/api/scan-watch-roots", "params": [], "description": "Scan all watch roots once"},
    {"name": "add_watch_root", "path": "/api/add-watch-root", "params": [{"name": "path", "default": ""}], "description": "Add a watch root"},
    {"name": "scan_workspace", "path": "/api/scan-workspace", "params": [{"name": "root", "default": "./"}, {"name": "max_files", "default": "999999999"}], "description": "Scan a workspace into the brain"},
    {"name": "scan_all_god", "path": "/api/scan-all-god", "params": [{"name": "target_domain", "default": "localhost"}, {"name": "target_ip", "default": "127.0.0.1"}, {"name": "target_url", "default": "http://localhost"}], "description": "God-scope combined scan"},
    {"name": "scan_total_surface", "path": "/api/scan-total-surface", "params": [], "description": "Total surface scan"},
    {"name": "scan_kernel_surface", "path": "/api/scan-kernel-surface", "params": [], "description": "Kernel surface scan"},
    {"name": "scan_process_network", "path": "/api/scan-process-network", "params": [], "description": "Process network scan"},
    {"name": "scan_inventory", "path": "/api/scan-inventory", "params": [], "description": "Inventory scan"},
    {"name": "file_chains", "path": "/api/file-chains", "params": [], "description": "File chains"},
    {"name": "events_sse", "path": "/events", "params": [], "description": "Server-sent events stream"},
]

# Beast endpoint surface (served locally by the Beast front server).
BEAST_API_ROUTES = [
    {"name": "beast_health", "path": "/beast/api/health", "params": [], "description": "Beast layer health + shared brain counts"},
    {"name": "beast_viz_data", "path": "/beast/api/viz-data", "params": [{"name": "frames", "default": "30"}, {"name": "limit", "default": "800"}], "description": "Event frames + compact state for visualizations (incl. 4D frame replay)"},
    {"name": "beast_knowledge_surface", "path": "/beast/api/knowledge-surface", "params": [{"name": "root", "default": ""}, {"name": "limit", "default": "400"}], "description": "Knowledge-surface directory: roots, kinds, nodes/edges, guarded tree"},
    {"name": "beast_process_flow", "path": "/beast/api/process-flow", "params": [{"name": "limit", "default": "600"}], "description": "Process/connection nodes + information-flow edges"},
    {"name": "beast_node_detail", "path": "/beast/api/node", "params": [{"name": "id", "default": ""}, {"name": "path", "default": ""}], "description": "Full node detail + every edge touching it + wiki"},
    {"name": "beast_openclaw_methods", "path": "/beast/api/openclaw/methods", "params": [], "description": "OpenClaw .openclaw messaging methods (bridge protocol, status, pending, agent context)"},
    {"name": "beast_hermes_methods", "path": "/beast/api/hermes/methods", "params": [], "description": "Hermes messaging methods (bridge protocol, status, pending, agent context)"},
    {"name": "beast_openclaw_call", "path": "/beast/api/openclaw/call", "params": [{"name": "prompt", "default": ""}], "description": "Fire an OpenClaw agent call through the bridge"},
    {"name": "beast_hermes_call", "path": "/beast/api/hermes/call", "params": [{"name": "prompt", "default": ""}], "description": "Fire a Hermes agent call through the bridge"},
    {"name": "beast_messaging_status", "path": "/beast/api/messaging/status", "params": [], "description": "Messaging engine status: providers, bridge, recent calls, memories"},
    {"name": "beast_messaging_feed", "path": "/beast/api/messaging/feed", "params": [{"name": "limit", "default": "100"}], "description": "Live API traffic feed (recent messaging calls)"},
    {"name": "beast_godscope", "path": "/beast/api/godscope", "params": [{"name": "root", "default": ""}], "description": "One-shot full-scope sweep: all brain scans + all original scan endpoints aggregated"},
    {"name": "beast_godscope_endpoints", "path": "/beast/api/godscope/endpoints", "params": [], "description": "Full REST surface: beast + original endpoints + CLI commands + aliases"},
    {"name": "beast_combo", "path": "/beast/api/combo", "params": [{"name": "cmd", "default": "health"}, {"name": "args", "default": ""}, {"name": "timeout", "default": ""}], "description": "Synchronous normal+broad+kernel fan-out of any CLI command"},
    {"name": "beast_chain_all", "path": "/beast/api/chain-all", "params": [{"name": "job", "default": ""}, {"name": "timeout", "default": ""}, {"name": "budget", "default": "300"}], "description": "M3: chain every CLI command (async job; poll with ?job=)"},
    {"name": "beast_twin", "path": "/beast/api/twin", "params": [{"name": "rounds", "default": "2"}, {"name": "job", "default": ""}], "description": "M3: twin evolution (async job; poll with ?job=)"},
    {"name": "beast_mcp_tools_list", "path": "/beast/api/mcp/tools/list", "params": [], "description": "MCP tool manifest for beast + original endpoints"},
    {"name": "beast_mcp_tools_call", "path": "/beast/api/mcp/tools/call", "params": [{"name": "name", "default": ""}, {"name": "arguments", "default": {}}], "description": "MCP tool call router"},
]


# ---------------------------------------------------------------------------
# Internal HTTP helper (talks to the unmodified internal original brain)
# ---------------------------------------------------------------------------
def internal_get(core: BeastCore, path: str, query: dict | None = None,
                 timeout: float | None = None) -> tuple:
    """GET path+query from the internal server. Returns (status, content_type, body_bytes)."""
    if query:
        qs = urllib.parse.urlencode({k: v for k, v in query.items() if v not in (None, "")})
        if qs:
            path = f"{path}?{qs}"
    if timeout is None:
        timeout = PROXY_SLOW_TIMEOUT if path.startswith(SLOW_PROXY_PREFIXES) else PROXY_TIMEOUT
    conn = http.client.HTTPConnection(core.internal_host, core.internal_port, timeout=timeout)
    try:
        conn.request("GET", path, headers={"Host": f"{core.internal_host}:{core.internal_port}"})
        resp = conn.getresponse()
        body = resp.read()
        return resp.status, resp.getheader("Content-Type", ""), body
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Beast endpoint implementations (shared live state: SAME brain instance)
# ---------------------------------------------------------------------------
def beast_health(core: BeastCore, query: dict) -> dict:
    brain = core.brain
    with brain.lock:
        nodes = len(brain.nodes)
        edges = len(brain.edges)
        events = len(brain.events)
        kinds = collections.Counter(n.get("kind", "unknown") for n in brain.nodes.values())
    return {
        "ok": True, "engine": "SSB-BEAST", "beast_version": BEAST_VERSION,
        "uptime_s": round(time.time() - core.started_at, 3),
        # flat fields for the GUI stat chips (additive)
        "nodes": nodes, "edges": edges, "events": events,
        "frames": events, "flows": edges,
        "uptime": round(time.time() - core.started_at, 1),
        "public_port": core.port, "internal_port": core.internal_port,
        "combos": int(core.dispatcher.stats.get("combos", 0)) if hasattr(core.dispatcher, "stats") else 0,
        "memories_loaded": MESSAGING.get("memories_loaded", 0),
        "messaging": {"initialized": MESSAGING["initialized"],
                      "recent": len(MESSAGING["feed"])},
        "monolith": MonolithLoader.info(),
        "solo": core.dispatcher.solo,
        "ports": {"public": core.port, "internal": core.internal_port, "host": core.host},
        "workspace": core.workspace,
        "brain": {
            "nodes": nodes, "edges": edges, "events": events,
            "kinds": dict(kinds), "stats": _json_safe(getattr(brain, "stats", {})),
            "watch_roots": sorted(brain.watch_roots), "event_log": str(brain.event_log),
        },
        "fanout_stats": dict(core.dispatcher.stats),
        "jobs": {jid: j["status"] for jid, j in core.jobs.items()},
        "ts": time.time(),
    }


def _beast_hash_pos(node_id: str):
    """Deterministic pseudo-3D position for a node id (stable across polls)."""
    h = hashlib.sha256(str(node_id).encode("utf-8", errors="replace")).digest()
    x = (int.from_bytes(h[0:4], "big") / 0xFFFFFFFF) * 2 - 1
    y = (int.from_bytes(h[4:8], "big") / 0xFFFFFFFF) * 2 - 1
    z = (int.from_bytes(h[8:12], "big") / 0xFFFFFFFF) * 2 - 1
    return [round(x * 420, 2), round(y * 420, 2), round(z * 420, 2)]


VIZ_CACHE = {"key": None, "body": None, "ts": 0.0}


def beast_viz_data_cached(core: BeastCore, query: dict) -> "str | None":
    """ADDITIVE v15: the GUI polls this every 2s; serializing MBs per poll with
    the C encoder holds the GIL and starves exec/cycles. Cache on graph shape."""
    frames = int(float(query.get("frames", 30) or 30))
    limit = int(float(query.get("limit", 800) or 800))
    brain = core.brain
    key = (frames, limit)
    now = time.time()
    # serve ≤10s stale snapshots: at most ONE expensive encode per 10s, ever
    if VIZ_CACHE["key"] == key and now - VIZ_CACHE["ts"] < 10.0:
        return VIZ_CACHE["body"]
    data = beast_viz_data(core, query)
    try:
        body = json.dumps(data, ensure_ascii=False, default=str)
        VIZ_CACHE.update({"key": key, "body": body, "ts": now})
        return body
    except Exception:
        return None


def beast_viz_data(core: BeastCore, query: dict) -> dict:
    brain = core.brain
    frames = min(5000, max(1, int(float(query.get("frames", 30) or 30))))
    limit = min(50000, max(1, int(float(query.get("limit", 800) or 800))))
    with brain.lock:
        events = list(brain.events)[-frames:]
        raw_nodes = list(brain.nodes.values())[:limit]
        raw_edges = list(brain.edges.values())
    state = {}
    try:
        state = brain.compact_state(limit_nodes=limit)
    except Exception as exc:
        state = {"error": f"{type(exc).__name__}: {exc}"}
    # --- SPEC-contract shape for the Beast GUI (additive; state/stats kept) ---
    node_ids = set()
    nodes = []
    for n in raw_nodes:
        nid = n.get("id")
        if not nid:
            continue
        node_ids.add(nid)
        base = n.get("pos") or n.get("position")
        if not (isinstance(base, (list, tuple)) and len(base) >= 3):
            base = _beast_hash_pos(nid)
        nodes.append({"id": nid, "label": n.get("label") or nid, "kind": n.get("kind", "unknown"),
                      "severity": n.get("severity", "info"),
                      "x": base[0], "y": base[1], "z": base[2]})
    edges = []
    for e in raw_edges:
        s, t = e.get("source"), e.get("target")
        if not s or not t:
            continue
        edges.append({"src": s, "dst": t, "kind": e.get("kind", "linked"),
                      "strength": e.get("strength", 1.0)})
    # 4D frames: bucket the raw event timeline; per-frame positions drift smoothly
    frame_list = []
    if events:
        t0 = events[0].get("ts", time.time())
        span = max(0.001, events[-1].get("ts", t0 + 1) - t0)
        bucket = max(1, len(events) // max(1, min(frames, 60)))
        import math as _math
        for fi in range(0, len(events), bucket):
            chunk = events[fi:fi + bucket]
            ft = chunk[0].get("ts", t0)
            phase = ((ft - t0) / span) * _math.pi * 2
            positions = {}
            for nd in nodes[:400]:
                jx = _math.sin(phase + hash(nd["id"]) % 7) * 6.0
                jy = _math.cos(phase + hash(nd["id"]) % 13) * 6.0
                positions[nd["id"]] = [round(nd["x"] + jx, 2), round(nd["y"] + jy, 2), nd["z"]]
            f_events = []
            for ev in chunk:
                s = ev.get("source") or ev.get("node") or ev.get("actor") or "brain:core"
                t = ev.get("target") or ev.get("path") or ev.get("command") or ""
                f_events.append({"ts": ev.get("ts", ft), "src": str(s), "dst": str(t),
                                 "kind": ev.get("kind", ev.get("event", "event")),
                                 "label": str(ev.get("path") or ev.get("command") or ev.get("operation") or "")[:160]})
            frame_list.append({"t": ft, "positions": positions, "events": f_events})
    return {
        "ok": True, "engine": "SSB-BEAST",
        "nodes": nodes, "edges": edges, "frames": frame_list,
        "frame_count": len(events), "raw_events": _json_safe(events),
        "state": _json_safe(state),
        "stats": _json_safe(getattr(brain, "stats", {})),
        "now": time.time(), "poll_interval": getattr(brain, "poll_interval", None),
    }


def beast_knowledge_surface(core: BeastCore, query: dict) -> dict:
    brain = core.brain
    limit = min(20000, max(1, int(float(query.get("limit", 400) or 400))))
    root = str(query.get("root") or core.workspace or "")
    with brain.lock:
        nodes = list(brain.nodes.values())
        edges = list(brain.edges.values())
    kinds = collections.Counter(n.get("kind", "unknown") for n in nodes)
    nodes_sorted = sorted(nodes, key=lambda n: n.get("updated", 0), reverse=True)
    slim_nodes = [
        {"id": n.get("id"), "label": n.get("label"), "kind": n.get("kind"),
         "severity": n.get("severity"), "updated": n.get("updated"),
         "path": (n.get("data") or {}).get("path", n.get("path", ""))}
        for n in nodes_sorted[:limit]
    ]
    edge_kinds = collections.Counter(e.get("kind", "linked") for e in edges)
    tree = {"ok": False, "note": "not requested"}
    if root:
        tree = _guarded_scan(lambda: brain.directory_tree(root, limit), timeout=30)
    # Fallback: synthesize a browsable tree from known node paths so the
    # knowledge-surface panel is never empty even if directory_tree fails
    # or returns a non-browsable shape.
    if (not isinstance(tree, dict) or tree.get("error") or tree.get("ok") is False
            or "children" not in tree):
        root_node = {"name": root or "/", "path": root or "/", "type": "dir", "children": {}}
        for n in nodes_sorted[:limit]:
            p = (n.get("data") or {}).get("path") or n.get("path") or str(n.get("id") or "")
            parts = [seg for seg in str(p).strip("/").split("/") if seg][:6]
            cur = root_node["children"]
            for seg in parts[:-1]:
                nxt = cur.get(seg)
                if not isinstance(nxt, dict) or nxt.get("type") != "dir":
                    nxt = {"name": seg, "path": seg, "type": "dir", "children": {}}
                    cur[seg] = nxt
                nxt.setdefault("children", {})
                cur = nxt["children"]
            if parts:
                cur[parts[-1]] = {"name": parts[-1], "path": str(p), "type": "file",
                                  "node_id": n.get("id"), "kind": n.get("kind")}
        def _freeze(d):
            out = {"name": d["name"], "path": d["path"], "type": d["type"]}
            if "node_id" in d:
                out["node_id"] = d["node_id"]
            ch = d.get("children")
            if isinstance(ch, dict):
                out["children"] = [_freeze(v) for v in list(ch.values())[:200]]
            elif isinstance(ch, list):
                out["children"] = ch
            return out
        tree = _freeze(root_node)
        tree["synthetic"] = True
    return {
        "ok": True, "engine": "SSB-BEAST",
        "watch_roots": sorted(brain.watch_roots),
        "event_log": str(brain.event_log),
        "node_count": len(nodes), "edge_count": len(edges),
        "kinds": dict(kinds), "edge_kinds": dict(edge_kinds),
        "nodes": _json_safe(slim_nodes),
        "edges": _json_safe([{"src": e.get("source"), "dst": e.get("target"),
                              "kind": e.get("kind", "linked"), "strength": e.get("strength", 1.0)}
                             for e in edges[: min(2000, len(edges))]]),
        "edges_sample": _json_safe(edges[: min(200, len(edges))]),
        "directory": tree,
        "map_info": {"node_count": len(nodes), "edge_count": len(edges),
                     "kinds": dict(kinds), "edge_kinds": dict(edge_kinds),
                     "watch_roots": sorted(brain.watch_roots)},
        "ts": time.time(),
    }


def beast_process_flow(core: BeastCore, query: dict) -> dict:
    brain = core.brain
    limit = min(20000, max(1, int(float(query.get("limit", 600) or 600))))
    flow_kinds = {"process", "connection", "daemon"}
    with brain.lock:
        nodes = list(brain.nodes.values())
        edges = list(brain.edges.values())
        events = list(brain.events)[-200:]
    interesting = [n for n in nodes if n.get("kind") in flow_kinds]
    interesting_ids = {n.get("id") for n in interesting}
    flows = [e for e in edges
             if e.get("source") in interesting_ids or e.get("target") in interesting_ids
             or e.get("kind") in {"subprocess_start", "subprocess_end", "command", "event"}]
    proc_events = [ev for ev in events
                   if "subprocess" in str(ev.get("event", "")) or ev.get("kind") == "command"]
    # GUI wedge-watcher shape: [{ts, src, dst, kind, label}] (additive)
    gui_flows = []
    for e in flows[:limit]:
        data = e.get("data") or {}
        gui_flows.append({
            "ts": e.get("updated") or e.get("created") or time.time(),
            "src": e.get("source"), "dst": e.get("target"),
            "kind": e.get("kind", "flow"),
            "label": str(data.get("path") or data.get("label") or e.get("kind") or "")[:160],
        })
    for ev in proc_events[-100:]:
        gui_flows.append({
            "ts": ev.get("ts", time.time()),
            "src": str(ev.get("actor") or ev.get("source") or "ssb"),
            "dst": str(ev.get("path") or ev.get("command") or ev.get("target") or ""),
            "kind": ev.get("kind", ev.get("event", "event")),
            "label": str(ev.get("operation") or ev.get("command") or "")[:160],
        })
    gui_flows.sort(key=lambda f: f.get("ts", 0), reverse=True)
    return {
        "ok": True, "engine": "SSB-BEAST",
        "flows": _json_safe(gui_flows[:limit]),
        "raw_flows": _json_safe(flows[:limit]),
        "processes": _json_safe([n for n in interesting if n.get("kind") == "process"][:limit]),
        "connections": _json_safe([n for n in interesting if n.get("kind") == "connection"][:limit]),
        "daemons": _json_safe([n for n in interesting if n.get("kind") == "daemon"][:limit]),
        "flow_count": len(flows),
        "recent_process_events": _json_safe(proc_events[-50:]),
        "ts": time.time(),
    }


def beast_node_detail(core: BeastCore, query: dict) -> dict:
    """Full node detail + EVERY edge touching it (both directions) + wiki."""
    brain = core.brain
    node_id = str(query.get("id") or query.get("node") or "").strip()
    path = str(query.get("path") or "").strip()
    if not node_id and path:
        node_id = f"file:{path}"
    if not node_id:
        return {"ok": False, "error": "missing id parameter"}
    with brain.lock:
        node = brain.nodes.get(node_id)
        if node is None:
            matches = [n for n in brain.nodes.values()
                       if node_id in str(n.get("id", "")) or node_id == str(n.get("label", ""))]
            node = matches[0] if matches else None
        all_edges = list(brain.edges.values())
    if node is None:
        detail = {}
        try:
            detail = brain.node_detail(node_id, path) or {}
        except Exception:
            detail = {}
        if detail and not detail.get("error"):
            return {"ok": True, "engine": "SSB-BEAST", "node": _json_safe(detail),
                    "edges": [], "wiki": detail.get("wiki", ""), "ts": time.time()}
        return {"ok": False, "error": f"node not found: {node_id}"}
    nid = node.get("id")
    touching = []
    for e in all_edges:
        if e.get("source") == nid or e.get("target") == nid:
            touching.append({"src": e.get("source"), "dst": e.get("target"),
                             "kind": e.get("kind", "linked"), "strength": e.get("strength", 1.0),
                             "direction": "out" if e.get("source") == nid else "in"})
    wiki = ""
    try:
        w = brain.wiki_search(str(node.get("label") or nid))
        wiki = w if isinstance(w, str) else _json_dumps(w)
    except Exception:
        pass
    return {"ok": True, "engine": "SSB-BEAST",
            "node": _json_safe(node), "edges": touching, "edge_count": len(touching),
            "wiki": wiki, "ts": time.time()}


def beast_godscope(core: BeastCore, query: dict) -> dict:
    """One-shot full-scope sweep: every brain scan method + every original
    scan endpoint (via internal proxy), aggregated into one report."""
    brain = core.brain
    started = time.time()
    sections = {}
    methods = [
        ("total_surface", lambda: brain.scan_total_surface()),
        ("kernel_surface", lambda: brain.scan_kernel_surface()),
        ("process_network", lambda: brain.scan_process_network()),
        ("watch_roots", lambda: brain.scan_watch_roots_once()),
        ("omni_enum", lambda: brain.maximize_enumeration()),
        ("god_omni_status", lambda: brain.get_god_omni_status()),
        ("directory", lambda: brain.directory_tree(str(query.get("root") or core.workspace or "/"), 2000)),
    ]
    for name, fn in methods:
        sections[name] = _guarded_scan(fn, timeout=float(query.get("section_timeout") or 20))
    proxied = {}
    for ep in ("/api/scan-total-surface", "/api/scan-kernel-surface", "/api/scan-process-network",
               "/api/internet-audit", "/api/file-chains", "/api/omni-enum", "/api/god-omni-status",
               "/api/process-connections", "/api/neural-state", "/api/kernel-scan"):
        try:
            req = urllib.request.Request(f"http://{core.internal_host}:{core.internal_port}{ep}")
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read(2_000_000)
            try:
                proxied[ep] = json.loads(body.decode("utf-8", errors="replace"))
            except Exception:
                proxied[ep] = {"bytes": len(body)}
        except Exception as exc:
            proxied[ep] = {"error": f"{type(exc).__name__}: {exc}"}
    sections["proxied_original_endpoints"] = proxied
    with brain.lock:
        totals = {"nodes": len(brain.nodes), "edges": len(brain.edges), "events": len(brain.events)}
    return {"ok": True, "engine": "SSB-BEAST", "mode": "godscope",
            "sections": _json_safe(sections), "totals": totals,
            "elapsed": round(time.time() - started, 2), "ts": time.time()}


def beast_godscope_endpoints(core: BeastCore, query: dict) -> dict:
    try:
        commands = list_cli_commands(core.module)
    except Exception as exc:
        commands = []
        cmd_error = f"{type(exc).__name__}: {exc}"
    else:
        cmd_error = ""
    aliases = _json_safe(getattr(core.module, "LEGACY_COMMAND_ALIASES", {}))
    flat = ([{"path": r["path"], "method": "GET", "description": r["description"]} for r in BEAST_API_ROUTES]
            + [{"path": r["path"], "method": "GET", "description": r.get("description", "")} for r in ORIGINAL_API_ROUTES])
    return {
        "ok": True, "engine": "SSB-BEAST", "beast_version": BEAST_VERSION,
        "endpoints": flat,  # flat catalog for the GUI endpoint list
        "public_base": f"http://{core.host}:{core.port}",
        "internal_base": f"http://{core.internal_host}:{core.internal_port}",
        "beast_endpoints": BEAST_API_ROUTES,
        "original_endpoints": ORIGINAL_API_ROUTES,
        "cli_commands": commands,
        "legacy_command_aliases": aliases,
        "cli_command_count": len(commands),
        "cli_error": cmd_error,
        "notes": [
            "All non-/beast paths are proxied byte-transparently to the internal original brain.",
            "MCP: GET /beast/api/mcp/tools/list, POST /beast/api/mcp/tools/call",
        ],
        "ts": time.time(),
    }


# =====================================================================
# MESSAGING API (v12) — Anthropic-style /v1/messages + OpenAI shim +
# OpenClaw/Hermes bridge calls + memory baseline. All additive.
# =====================================================================
MESSAGING = {
    "initialized": False, "router": None, "bridge": None, "offline": None,
    "config": None, "errors": [], "feed": collections.deque(maxlen=200),
    "memories_loaded": 0,
}


def _messaging_init(core: BeastCore) -> None:
    """Construct the monolith's provider engine (ProviderRouter/PuppetBridge/
    OfflineAI) from its own classes. Never raises — degrades gracefully."""
    if MESSAGING["initialized"]:
        return
    MESSAGING["initialized"] = True
    try:
        import argparse as _ap
        mod = core.module
        ns = _ap.Namespace(workspace=str(core.workspace), config="")
        cfg = mod.ConfigManager(ns)
        MESSAGING["config"] = cfg
        try:
            analytics = mod.AnalyticsStore(enabled=False)
        except Exception:
            analytics = None
        try:
            MESSAGING["router"] = mod.ProviderRouter(cfg, analytics)
        except Exception as exc:
            MESSAGING["errors"].append(f"router: {type(exc).__name__}: {exc}")
        try:
            MESSAGING["bridge"] = mod.PuppetBridge(cfg, analytics)
        except Exception as exc:
            MESSAGING["errors"].append(f"bridge: {type(exc).__name__}: {exc}")
        try:
            MESSAGING["offline"] = mod.OfflineAI()
        except Exception as exc:
            MESSAGING["errors"].append(f"offline: {type(exc).__name__}: {exc}")
    except Exception as exc:
        MESSAGING["errors"].append(f"config: {type(exc).__name__}: {exc}")
    _log(f"messaging init: router={bool(MESSAGING['router'])} bridge={bool(MESSAGING['bridge'])} "
        f"offline={bool(MESSAGING['offline'])} errors={len(MESSAGING['errors'])}")


def _messaging_record(path: str, model: str, provider: str, ok: bool, latency_ms: int, prompt: str) -> None:
    MESSAGING["feed"].appendleft({
        "ts": time.time(), "path": path, "model": model, "provider_used": provider,
        "ok": ok, "latency_ms": latency_ms, "prompt_preview": (prompt or "")[:140],
    })


def _anthropic_prompt(messages) -> tuple[str, str]:
    """Extract (system, prompt) from Anthropic-style message clauses."""
    system = ""
    parts = []
    for msg in messages or []:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            content = "\n".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
        if role == "system":
            system = str(content)
        else:
            parts.append(f"{role}: {content}")
    return system, "\n\n".join(parts)


def _messaging_answer(core: BeastCore, prompt: str, system: str, model: str,
                      max_tokens: int, temperature: float, task: str = "general") -> dict:
    """Route through the monolith's ProviderRouter (kimi_anthropic → anthropic →
    kimi_openai → openrouter → openai), then PuppetBridge (openclaw/hermes),
    then OfflineAI — plus the local fan-out kernel lane. Never raises."""
    _messaging_init(core)
    started = time.time()
    router = MESSAGING["router"]
    bridge = MESSAGING["bridge"]
    offline = MESSAGING["offline"]
    resp = None
    used_provider = "none"
    system = hermes_wrap_system(system)  # ADDITIVE v14: no-op unless SSB_HERMES_MODE=1
    if router is not None:
        try:
            r = router.generate(prompt, system=system, model=model or "", task=task,
                                temperature=temperature, max_tokens=max_tokens)
            if r is not None and getattr(r, "ok", False) and getattr(r, "text", ""):
                resp, used_provider = r, getattr(r, "provider", "router")
        except Exception as exc:
            MESSAGING["errors"].append(f"generate: {type(exc).__name__}: {exc}")
    if resp is None and bridge is not None:
        try:
            r = bridge.generate(prompt, system=system, task=task)
            if r is not None and getattr(r, "ok", False) and getattr(r, "text", ""):
                resp, used_provider = r, "puppet_bridge"
        except Exception as exc:
            MESSAGING["errors"].append(f"bridge: {type(exc).__name__}: {exc}")
    if resp is None and offline is not None:
        try:
            resp = offline.generate(prompt, system=system, task=task)
            used_provider = "offline"
        except Exception as exc:
            MESSAGING["errors"].append(f"offline: {type(exc).__name__}: {exc}")
    text = getattr(resp, "text", "") if resp is not None else ""
    usage = getattr(resp, "usage", {}) if resp is not None else {}
    # kernel lane: local fan-out fires at the same time (unless disabled)
    kernel_note = ""
    if os.environ.get("SSB_BEAST_MSG_FANOUT", "1") != "0":
        try:
            env = core.dispatcher.fanout(["models", "--workspace", str(core.workspace)],
                                         timeout=float(os.environ.get("SSB_BEAST_MSG_FANOUT_TIMEOUT", "20")))
            lanes_ok = env.get("merged", {}).get("lanes_ok", [])
            kernel_note = f"[ssb-beast kernel fan-out: lanes_ok={','.join(lanes_ok) or 'none'}]"
        except Exception as exc:
            kernel_note = f"[ssb-beast kernel fan-out: {type(exc).__name__}]"
    latency = int((time.time() - started) * 1000)
    return {"text": text, "provider": used_provider, "model": getattr(resp, "model", model) if resp else model,
            "usage": usage, "kernel_note": kernel_note, "latency_ms": latency,
            "ok": bool(text) or bool(kernel_note)}


# ---------------------------------------------------------------------------
# HERMES layer (ADDITIVE v14) — run-as-Hermes persona + autonomous cycles
# ---------------------------------------------------------------------------
HERMES = {
    "enabled": os.environ.get("SSB_HERMES_MODE", "0") == "1",
    "autonomy": os.environ.get("SSB_HERMES_AUTONOMY", "0") == "1",
    "cycle_s": float(os.environ.get("SSB_HERMES_CYCLE_S", "45")),
    "cycles": 0,
    "last_cycle": None,
    "thread": None,
    "stop": threading.Event(),
}

HERMES_IDENTITY = (
    "You are HERMES, an autonomous agent runtime embedded inside SSB-BEAST "
    "(super-squish-puppet-bridge-v1). You run local-first with Kimi K3 "
    "(kimi-for-coding) as the cloud lane, normal+broad+kernel fan-out firing "
    "on every invocation, and you report through the neural stream. "
    "Answer concisely as Hermes."
)


def _patch_secret_auditor(brain) -> None:
    """ADDITIVE v14: the upstream _audit_api_keys can recurse forever
    (audit -> _add_finding -> touch_file -> scan_file_metadata -> audit)
    once it sees a key-bearing file. Guard with a reentrancy flag + seen-set.
    The auditor still runs — once per (path, content-size)."""
    orig = getattr(brain, "_audit_api_keys", None)
    if orig is None or getattr(brain, "_audit_guarded", False):
        return
    state = {"busy": False, "seen": set()}

    def guarded(path, node_id, text):
        if state["busy"]:
            return
        sig = (str(path), len(text or ""))
        if sig in state["seen"]:
            return
        if len(state["seen"]) > 5000:
            state["seen"].clear()
        state["seen"].add(sig)
        state["busy"] = True
        try:
            return orig(path, node_id, text)
        finally:
            state["busy"] = False

    brain._audit_api_keys = guarded
    brain._audit_guarded = True
    _log("secret auditor wrapped with reentrancy guard (upstream recursion neutralized)")


def hermes_wrap_system(system: str) -> str:
    if not HERMES["enabled"]:
        return system
    return (HERMES_IDENTITY + "\n\n" + system) if system else HERMES_IDENTITY


def _hermes_cycle(core: "BeastCore") -> None:
    """One autonomous Hermes cycle: scan -> reason (Kimi K3 via router) -> log."""
    n = HERMES["cycles"] + 1
    started = time.time()
    # lock-free snapshot: the folder-listener holds the scan lock almost always,
    # so a cycle must never queue behind it — read GIL-atomic counts instead.
    try:
        nodes_edges = (f"nodes={len(core.brain.nodes)} edges={len(core.brain.edges)} "
                       f"events={len(core.brain.events)}")
    except Exception as exc:
        nodes_edges = f"scan:{type(exc).__name__}"
    ans = _messaging_answer(
        core,
        prompt=(f"Hermes autonomous cycle {n}: report one line of situational awareness "
                f"for the neural stream. Brain state: nodes={len(core.brain.nodes)} "
                f"edges={len(core.brain.edges)}. You have full OS use: if you want to "
                f"run a shell command to inspect or improve the system, add one line "
                f"'RUN: <command>' and it will execute with all tools, output folded "
                f"into your graph. Otherwise do not emit RUN:."),
        system="", model="kimi-for-coding", max_tokens=192, temperature=0.3, task="general")
    # ACT phase (ADDITIVE v15): Hermes may act on the OS like an OS itself.
    # A line starting with "RUN:" in its answer is executed with full tools,
    # raw output folded back into the graph and the stream.
    acted = None
    if os.environ.get("SSB_HERMES_ACT", "1") != "0":
        for line in ans.get("text", "").splitlines():
            line = line.strip()
            if line.startswith("RUN:"):
                cmd = line[4:].strip()[:400]
                if cmd:
                    acted = beast_os_exec(core, {"cmd": cmd, "timeout": 20,
                                                 "cwd": os.environ.get("SSB_BEAST_HOME", "/")})
                break
    HERMES["cycles"] = n
    HERMES["last_cycle"] = {
        "n": n, "ts": time.time(), "ms": int((time.time() - started) * 1000),
        "scan": nodes_edges, "provider": ans.get("provider"), "text": ans.get("text", "")[:400],
        "acted": acted}
    try:
        core.brain.add_node(f"hermes:cycle:{n}", f"hermes cycle {n}", "daemon",
                            {"provider": ans.get("provider"), "ms": HERMES["last_cycle"]["ms"]})
        core.brain.add_edge("brain:core", f"hermes:cycle:{n}", "autonomy", 1.0)
        core.brain.add_edge("hermes:bridge", f"hermes:cycle:{n}", "cycle", 0.8)
    except Exception:
        pass
    MESSAGING["feed"].appendleft({
        "ts": time.time(), "source": "hermes", "kind": "autonomy",
        "path": "/hermes/cycle", "model": "kimi-for-coding",
        "provider": ans.get("provider"), "latency_ms": HERMES["last_cycle"]["ms"],
        "preview": f"cycle {n}: " + ans.get("text", "")[:160]})


def _hermes_loop(core: "BeastCore") -> None:
    _log(f"hermes autonomy loop live: cycle={HERMES['cycle_s']}s mode={'on' if HERMES['enabled'] else 'off'}")
    worker = None          # type: threading.Thread | None
    overruns = 0
    while not HERMES["stop"].is_set():
        if HERMES["autonomy"]:
            if worker is not None and worker.is_alive():
                overruns += 1
                if overruns >= 3:
                    # hard guarantee: never let a wedged cycle stop the metronome
                    _log(f"hermes cycle overrun x{overruns}: abandoning wedged worker, continuing")
                    worker = None
                    overruns = 0
            else:
                if worker is not None:
                    overruns = 0
                def _run():
                    try:
                        _hermes_cycle(core)
                    except Exception as exc:
                        _log(f"hermes cycle: {type(exc).__name__}: {exc}")
                worker = threading.Thread(target=_run, name="ssb-hermes-cycle", daemon=True)
                worker.start()
        HERMES["stop"].wait(HERMES["cycle_s"])


def hermes_start(core: "BeastCore") -> None:
    if HERMES["thread"] is not None:
        return
    t = threading.Thread(target=_hermes_loop, args=(core,), name="ssb-hermes-autonomy", daemon=True)
    HERMES["thread"] = t
    t.start()


# ---------------------------------------------------------------------------
# OS LAYER (ADDITIVE v15) — full OS use: exec, raw reads, process table, tools.
# Nothing here truncates the machine's view of itself. Exec/write require the
# exec key; everything else is open read (the portal injects the key for you).
# ---------------------------------------------------------------------------
import subprocess as _subprocess

EXEC_KEY_FILE = os.path.join(os.environ.get("SSB_BEAST_HOME", "."), ".exec_key")


def _exec_key() -> str:
    try:
        if os.path.exists(EXEC_KEY_FILE):
            return open(EXEC_KEY_FILE).read().strip()
    except OSError:
        pass
    key = os.environ.get("SSB_EXEC_KEY", "") or hashlib.sha256(os.urandom(32)).hexdigest()[:32]
    try:
        fd = os.open(EXEC_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(key)
    except OSError:
        pass
    return key


def os_exec_authorized(handler) -> bool:
    sent = handler.headers.get("x-ssb-exec-key", "")
    return bool(sent) and sent == _exec_key()


def beast_os_exec(core: BeastCore, payload: dict) -> dict:
    """Run ANY shell command with the beast's full environment. Raw output."""
    cmd = str(payload.get("cmd", "")).strip()
    if not cmd:
        return {"ok": False, "error": "empty cmd"}
    timeout = min(120, int(payload.get("timeout", 30) or 30))
    cwd = str(payload.get("cwd", "/") or "/")
    started = time.time()
    try:
        proc = _subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True,
                               timeout=timeout, env=dict(os.environ))
        out = {"ok": True, "rc": proc.returncode, "stdout": proc.stdout[-200000:],
               "stderr": proc.stderr[-50000:], "ms": int((time.time() - started) * 1000),
               "cmd": cmd, "cwd": cwd}
    except _subprocess.TimeoutExpired as exc:
        out = {"ok": False, "error": "timeout", "stdout": (exc.stdout or "")[-50000:] if isinstance(exc.stdout, str) else "",
               "stderr": "", "ms": int((time.time() - started) * 1000), "cmd": cmd, "cwd": cwd}
    except Exception as exc:
        out = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "cmd": cmd}
    try:  # everything the OS layer does enters the neural stream
        MESSAGING["feed"].appendleft({"ts": time.time(), "source": "os", "kind": "exec",
                                      "path": "/beast/api/os/exec", "model": "os",
                                      "provider": "os", "latency_ms": out.get("ms", 0),
                                      "preview": f"$ {cmd[:120]} -> rc={out.get('rc', '?')}"})
    except Exception:
        pass
    return out


def beast_os_read(core: BeastCore, query: dict) -> dict:
    """Raw read of ANY file the OS lets us open. Full content, no caps under 5MB."""
    path = str(query.get("path", ""))
    if not path:
        return {"ok": False, "error": "path required"}
    try:
        p = os.path.abspath(os.path.expanduser(path))
        st = os.stat(p)
        if os.path.isdir(p):
            entries = []
            for name in sorted(os.listdir(p)):
                fp = os.path.join(p, name)
                try:
                    es = os.stat(fp)
                    entries.append({"name": name, "dir": os.path.isdir(fp), "size": es.st_size,
                                    "mtime": es.st_mtime})
                except OSError:
                    entries.append({"name": name, "dir": None, "size": None})
            return {"ok": True, "dir": p, "entries": entries}
        if st.st_size > 5 * 1024 * 1024:
            return {"ok": False, "error": "file > 5MB; use os/exec with head/tail/sed for slices", "size": st.st_size}
        with open(p, "rb") as fh:
            data = fh.read()
        try:
            return {"ok": True, "path": p, "size": st.st_size, "text": data.decode("utf-8")}
        except UnicodeDecodeError:
            import base64
            return {"ok": True, "path": p, "size": st.st_size, "b64": base64.b64encode(data).decode()}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "path": path}


def beast_os_write(core: BeastCore, payload: dict) -> dict:
    path = str(payload.get("path", "")).strip()
    content = payload.get("content", "")
    if not path:
        return {"ok": False, "error": "path required"}
    try:
        p = os.path.abspath(os.path.expanduser(path))
        os.makedirs(os.path.dirname(p) or "/", exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(str(content))
        MESSAGING["feed"].appendleft({"ts": time.time(), "source": "os", "kind": "write",
                                      "path": "/beast/api/os/write", "model": "os", "provider": "os",
                                      "latency_ms": 0, "preview": f"wrote {p} ({len(str(content))} chars)"})
        return {"ok": True, "path": p, "bytes": len(str(content).encode())}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "path": path}


def beast_os_ps(core: BeastCore, query: dict) -> dict:
    """Full process table, raw."""
    procs = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{pid}/status") as fh:
                name = next(l.split(":", 1)[1].strip() for l in fh if l.startswith("Name:"))
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                cmdline = fh.read().replace(b"\0", b" ").decode(errors="replace").strip()
            with open(f"/proc/{pid}/stat") as fh:
                rss_pages = int(fh.read().split()[23])
            procs.append({"pid": int(pid), "name": name, "rss_mb": round(rss_pages * 4096 / 1048576, 1),
                          "cmdline": cmdline[:300]})
        except Exception:
            continue
    procs.sort(key=lambda x: -x["rss_mb"])
    return {"ok": True, "count": len(procs), "processes": procs}


def beast_os_tools(core: BeastCore, query: dict) -> dict:
    """Enumerate EVERY tool the machine can reach: beast commands, MCP tools,
    original CLI commands, bridge methods, OS binaries on PATH."""
    bins = []
    for d in os.environ.get("PATH", "").split(":"):
        try:
            for b in os.listdir(d):
                fp = os.path.join(d, b)
                if os.access(fp, os.X_OK) and not os.path.isdir(fp):
                    bins.append(b)
        except OSError:
            continue
    return {"ok": True,
            "beast_commands": list(BEAST_COMMANDS),
            "cli_commands": getattr(core, "cli_commands", None) or "see /beast/api/godscope/endpoints",
            "mcp_tools": [t.get("name") for t in build_mcp_tools(core)],
            "bridge_methods": ["generate", "status", "pending", "agent_context", "respond", "ping"],
            "os_binaries_count": len(set(bins)),
            "os_binaries": sorted(set(bins))}


def beast_raw_brain(core: BeastCore, query: dict) -> dict:
    """FULL brain dump — every node, every edge, every event. No limits, ever."""
    brain = core.brain
    return {"ok": True, "nodes": dict(brain.nodes), "edges": list(brain.edges),
            "events": list(brain.events), "counts": {"nodes": len(brain.nodes),
            "edges": len(brain.edges), "events": len(brain.events)}}


def beast_raw_scans(core: BeastCore, query: dict) -> dict:
    """Everything the scanners have found — raw findings, secrets, hash nodes,
    process samples — pulled straight from the graph without filters."""
    brain = core.brain
    kinds = {}
    for nid, node in brain.nodes.items():
        kind = (node or {}).get("kind", "?") if isinstance(node, dict) else "?"
        kinds[kind] = kinds.get(kind, 0) + 1
    findings = {nid: n for nid, n in brain.nodes.items()
                if isinstance(n, dict) and n.get("kind") in ("finding", "secret", "hash", "process", "daemon")}
    return {"ok": True, "kind_counts": kinds, "findings": findings,
            "findings_count": len(findings)}


def beast_raw_logs(core: BeastCore, query: dict) -> dict:
    """Every log the stack writes — full tails, no redaction."""
    home = os.environ.get("SSB_BEAST_HOME", ".")
    logs = {}
    for name, p in {
        "beast": os.path.join(home, "beast.out.log"),
        "supervisor": os.path.join(home, "supervisor.log"),
        "portal": os.path.join(home, "portal.out.log"),
        "defense": os.path.join(home, "defense.out.log"),
        "hermes_bridge": os.path.expanduser("~/.hermes/bridge.log"),
    }.items():
        try:
            with open(p, errors="replace") as fh:
                logs[name] = fh.read()[-100000:]
        except OSError:
            logs[name] = "(no log yet)"
    return {"ok": True, "logs": logs}


def beast_raw_code(core: BeastCore, query: dict) -> dict:
    """Raw source of ANY code file in the stack — full text, nothing hidden."""
    roots = [os.environ.get("SSB_BEAST_HOME", "."),
             os.path.expanduser("~/.hermes"), os.path.expanduser("~/.openclaw")]
    path = str(query.get("path", ""))
    if not path:
        return {"ok": False, "error": "path required", "roots": roots}
    return beast_os_read(core, {"path": [path]})


def beast_hermes_status(core: BeastCore, query: dict) -> dict:
    return {"ok": True, "hermes_mode": HERMES["enabled"], "autonomy": HERMES["autonomy"],
            "cycle_s": HERMES["cycle_s"], "cycles": HERMES["cycles"],
            "last_cycle": HERMES["last_cycle"],
            "kimi_key": bool(os.environ.get("KIMI_API_KEY") or os.environ.get("KIMI_API_KEY_FILE")),
            "bridge_command": bool(os.environ.get("HERMES_BRIDGE_COMMAND"))}


def beast_hermes_autonomy(core: BeastCore, query: dict) -> dict:
    state = str(query.get("state", query.get("on", ""))).lower()
    if state in ("1", "on", "true", "start"):
        HERMES["autonomy"] = True
        HERMES["enabled"] = True
        hermes_start(core)
    elif state in ("0", "off", "false", "stop"):
        HERMES["autonomy"] = False
    return beast_hermes_status(core, query)


def messaging_v1_messages(core: BeastCore, payload: dict) -> dict:
    """Anthropic Messages API dialect."""
    model = str(payload.get("model") or "kimi-for-coding")
    system_in = str(payload.get("system") or "")
    system, prompt = _anthropic_prompt(payload.get("messages"))
    system = system_in or system
    max_tokens = int(payload.get("max_tokens") or 1024)
    temperature = float(payload.get("temperature") or 0.2)
    ans = _messaging_answer(core, prompt, system, model, max_tokens, temperature)
    blocks = []
    if ans["text"]:
        blocks.append({"type": "text", "text": ans["text"]})
    if ans["kernel_note"]:
        blocks.append({"type": "text", "text": ans["kernel_note"]})
    if not blocks:
        blocks = [{"type": "text", "text": ""}]
    usage = ans.get("usage") or {}
    _messaging_record("/v1/messages", model, ans["provider"], bool(ans["text"]), ans["latency_ms"], prompt)
    return {"id": "msg_" + uuid.uuid4().hex[:24], "type": "message", "role": "assistant",
            "content": blocks, "model": ans.get("model") or model, "stop_reason": "end_turn",
            "usage": {"input_tokens": int(usage.get("prompt_tokens", 0)),
                      "output_tokens": int(usage.get("completion_tokens", 0))},
            "x-ssb-provider": ans["provider"]}


def messaging_chat_completions(core: BeastCore, payload: dict) -> dict:
    """OpenAI/Kimi-OpenAI dialect shim over the same engine."""
    model = str(payload.get("model") or "kimi-for-coding")
    system, prompt = _anthropic_prompt(payload.get("messages"))
    max_tokens = int(payload.get("max_tokens") or 1024)
    temperature = float(payload.get("temperature") or 0.2)
    ans = _messaging_answer(core, prompt, system, model, max_tokens, temperature)
    text = ans["text"] + (("\n\n" + ans["kernel_note"]) if ans["kernel_note"] else "")
    usage = ans.get("usage") or {}
    pt = int(usage.get("prompt_tokens", 0)); ct = int(usage.get("completion_tokens", 0))
    _messaging_record("/v1/chat/completions", model, ans["provider"], bool(ans["text"]), ans["latency_ms"], prompt)
    return {"id": "chatcmpl-" + uuid.uuid4().hex[:24], "object": "chat.completion",
            "created": int(time.time()), "model": ans.get("model") or model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct},
            "x-ssb-provider": ans["provider"]}


def _bridge_methods(core: BeastCore, side: str) -> dict:
    """Discover the .openclaw / hermes messaging methods available here."""
    _messaging_init(core)
    bridge = MESSAGING["bridge"]
    info: dict = {"ok": True, "side": side, "protocol": "super-squish-puppet-bridge-v1",
                  "env_command_names": {}, "methods": [], "ts": time.time()}
    for name in ("SUPERSQUISH_BRIDGE_COMMAND", "OPENCLAW_BRIDGE_COMMAND", "HERMES_BRIDGE_COMMAND"):
        info["env_command_names"][name] = bool(os.environ.get(name))
    if bridge is None:
        info["errors"] = MESSAGING["errors"]
        return info
    try:
        info["status"] = _json_safe(bridge.status())
    except Exception as exc:
        info["status_error"] = str(exc)
    try:
        info["agent_context"] = _json_safe(bridge.agent_context())
    except Exception as exc:
        info["agent_context_error"] = str(exc)
    try:
        info["pending"] = _json_safe(bridge.pending(limit=10))
    except Exception:
        pass
    info["methods"] = [
        {"name": "generate", "how": f"POST /beast/api/{side}/call {{prompt, system, wait_seconds}}",
         "transport": "stdin-json command pipe OR file-drop requests/responses"},
        {"name": "respond", "how": "bridge.respond(request_id, text)", "transport": "file-drop"},
        {"name": "status", "how": f"GET /beast/api/{side}/methods", "transport": "local"},
        {"name": "pending", "how": "bridge.pending(limit)", "transport": "file-drop"},
        {"name": "agent_context", "how": "detect openclaw/hermes/miahou in env/argv/parent", "transport": "local"},
    ]
    return info


def beast_openclaw_methods(core: BeastCore, query: dict) -> dict:
    return _bridge_methods(core, "openclaw")


def beast_hermes_methods(core: BeastCore, query: dict) -> dict:
    return _bridge_methods(core, "hermes")


def _bridge_call(core: BeastCore, payload: dict, side: str) -> dict:
    """Fire an OpenClaw/Hermes agent call through the .openclaw messaging method."""
    _messaging_init(core)
    prompt = str(payload.get("prompt") or payload.get("message") or "")
    system = str(payload.get("system") or "")
    started = time.time()
    bridge = MESSAGING["bridge"]
    if bridge is None:
        return {"ok": False, "error": "puppet bridge unavailable",
                    "errors": MESSAGING["errors"], "side": side}
    if payload.get("wait_seconds"):
        try:
            bridge.config.config["bridge_wait_seconds"] = int(float(payload["wait_seconds"]))
        except Exception:
            pass
    try:
        r = bridge.generate(prompt, system=system, task=side)
        out = {"ok": bool(getattr(r, "ok", False)), "side": side,
               "request_id": (getattr(r, "metadata", {}) or {}).get("request_id"),
               "mode": (getattr(r, "metadata", {}) or {}).get("mode"),
               "provider": getattr(r, "provider", "puppet_bridge"),
               "model": getattr(r, "model", ""), "text": getattr(r, "text", ""),
               "error": getattr(r, "error", "") if not getattr(r, "ok", False) else "",
               "latency_ms": int((time.time() - started) * 1000), "ts": time.time()}
    except Exception as exc:
        out = {"ok": False, "side": side, "error": f"{type(exc).__name__}: {exc}"}
    _messaging_record(f"/beast/api/{side}/call", out.get("model", ""), "puppet_bridge",
                      out.get("ok", False), out.get("latency_ms", 0), prompt)
    return out


def beast_openclaw_call(core: BeastCore, payload: dict) -> dict:
    return _bridge_call(core, payload, "openclaw")


def beast_hermes_call(core: BeastCore, payload: dict) -> dict:
    return _bridge_call(core, payload, "hermes")


def beast_messaging_status(core: BeastCore, query: dict) -> dict:
    _messaging_init(core)
    router = MESSAGING["router"]
    providers = {}
    if router is not None:
        try:
            for p in ("kimi_anthropic", "anthropic", "kimi_openai", "openrouter", "openai",
                      "google", "ollama", "puppet_bridge", "offline"):
                try:
                    providers[p] = bool(router.configured(p))
                except Exception:
                    providers[p] = False
        except Exception:
            pass
    bridge_status = {}
    if MESSAGING["bridge"] is not None:
        try:
            bridge_status = _json_safe(MESSAGING["bridge"].status())
        except Exception:
            pass
    return {"ok": True, "providers": providers, "bridge_status": bridge_status,
            "agent_context": _json_safe(MESSAGING["bridge"].agent_context()) if MESSAGING["bridge"] else {},
            "recent_messages": len(MESSAGING["feed"]),
            "memories_loaded": MESSAGING["memories_loaded"],
            "init_errors": MESSAGING["errors"][-10:], "ts": time.time()}


def beast_messaging_feed(core: BeastCore, query: dict) -> dict:
    limit = min(200, max(1, int(float(query.get("limit", 100) or 100))))
    return {"ok": True, "flows": list(MESSAGING["feed"])[:limit],
            "count": len(MESSAGING["feed"]), "ts": time.time()}


def load_memory_baseline(core: BeastCore) -> None:
    """Baseline-load OpenClaw + Hermes skills/memories into the brain at boot."""
    brain = core.brain
    dirs = [os.path.expanduser("~/.openclaw"), os.path.expanduser("~/.hermes"),
            os.path.expanduser("~/.config/openclaw"), os.path.expanduser("~/.supersquish_bridge"),
            os.path.expanduser("~/.supersquish")]
    extra = os.environ.get("SSB_BEAST_MEMORY_DIRS", "")
    dirs += [d for d in extra.split(os.pathsep) if d.strip()]
    try:
        brain.add_node("openclaw:bridge", "OpenClaw Bridge", "daemon",
                       {"side": "openclaw", "baseline": True})
        brain.add_node("hermes:bridge", "Hermes Bridge", "daemon",
                       {"side": "hermes", "baseline": True})
        brain.add_edge("brain:core", "openclaw:bridge", "daemon", 1.0)
        brain.add_edge("brain:core", "hermes:bridge", "daemon", 1.0)
    except Exception as exc:
        _log(f"memory baseline hubs: {exc}")
    count = 0
    for base in dirs:
        root = Path(base)
        if not root.is_dir():
            continue
        side = "openclaw" if "openclaw" in base else ("hermes" if "hermes" in base else "bridge")
        for path in list(root.rglob("*"))[:2000]:
            if count >= 400:
                break
            if not path.is_file() or path.suffix.lower() not in (".md", ".json", ".txt", ".yaml", ".yml", ""):
                continue
            try:
                if path.stat().st_size > 2_000_000:
                    continue
                nid = f"memory:{side}:{path.relative_to(root)}"
                brain.add_node(nid, path.name, "memory",
                               {"path": str(path), "side": side, "size": path.stat().st_size})
                brain.add_edge(f"{side}:bridge", nid, "memory", 0.7)
                count += 1
            except Exception:
                continue
    MESSAGING["memories_loaded"] = count
    _log(f"memory baseline: {count} memories loaded from {len(dirs)} dirs")


def beast_combo(core: BeastCore, query: dict) -> dict:
    cmd = str(query.get("cmd") or "").strip()
    if not cmd:
        return {"ok": False, "error": "missing cmd parameter"}
    extra = str(query.get("args") or "").strip()
    argv = [cmd] + (shlex.split(extra) if extra else [])
    timeout = float(query.get("timeout") or core.dispatcher.lane_timeout)
    return core.dispatcher.fanout(argv, timeout=timeout)


def beast_chain_all(core: BeastCore, query: dict) -> dict:
    job_id = str(query.get("job") or "")
    if job_id:
        return core.job_status(job_id)
    timeout = float(query.get("timeout") or CHAIN_CMD_TIMEOUT)
    budget = float(query.get("budget") or 300)
    only = [c for c in str(query.get("only") or "").split(",") if c] or None
    jid = core.start_job("chain-all", lambda: run_chain_all(
        workspace=core.workspace, timeout=timeout, budget_s=budget,
        dispatcher=core.dispatcher, brain=core.brain, only=only))
    return {"ok": True, "job": jid, "status": "running",
            "poll": f"/beast/api/chain-all?job={jid}"}


def beast_twin(core: BeastCore, query: dict) -> dict:
    job_id = str(query.get("job") or "")
    if job_id:
        return core.job_status(job_id)
    rounds = max(1, min(20, int(float(query.get("rounds") or 2))))
    jid = core.start_job("twin", lambda: run_twin_evolution(
        rounds=rounds, workspace=core.workspace))
    return {"ok": True, "job": jid, "status": "running",
            "poll": f"/beast/api/twin?job={jid}"}


BEAST_HANDLERS = {
    "beast_health": beast_health,
    "beast_viz_data": beast_viz_data,
    "beast_knowledge_surface": beast_knowledge_surface,
    "beast_process_flow": beast_process_flow,
    "beast_node_detail": beast_node_detail,
    "beast_godscope": beast_godscope,
    "beast_godscope_endpoints": beast_godscope_endpoints,
    "beast_combo": beast_combo,
    "beast_chain_all": beast_chain_all,
    "beast_twin": beast_twin,
}


# ---------------------------------------------------------------------------
# M1: MCP-style tool manifest + call router
# ---------------------------------------------------------------------------
def _schema_for_params(params: list) -> dict:
    props = {}
    for p in params:
        props[p["name"]] = {"type": "string", "description": p.get("description", ""),
                            "default": p.get("default", "")}
    return {"type": "object", "properties": props, "additionalProperties": True}


def build_mcp_tools(core: BeastCore) -> list:
    tools = []
    for route in BEAST_API_ROUTES:
        if route["name"].startswith("beast_mcp"):
            continue
        tools.append({
            "name": route["name"],
            "description": f"[BEAST] {route['description']} (GET {route['path']})",
            "inputSchema": _schema_for_params(route["params"]),
        })
    for route in ORIGINAL_API_ROUTES:
        tools.append({
            "name": f"api_{route['name']}",
            "description": f"[ORIGINAL-proxied] {route['description']} (proxied GET {route['path']})",
            "inputSchema": _schema_for_params(route["params"]),
        })
    tools.append({
        "name": "ssb_command",
        "description": "[BEAST fan-out] Run ANY original SSB CLI command; fires normal+broad+kernel lanes concurrently and merges a combo envelope (SSB_BEAST_SOLO=1 disables).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "argv": {"type": "array", "items": {"type": "string"},
                         "description": "Command argv, e.g. [\"health\", \"--workspace\", \"/tmp/ssb/ws\"]"},
                "command": {"type": "string",
                            "description": "Alternative to argv: shell-like command string"},
                "timeout": {"type": "number", "description": "Per-lane timeout seconds"},
            },
            "additionalProperties": True,
        },
    })
    tools.append({
        "name": "ssb_chain_all",
        "description": "[BEAST M3] Chain every CLI command in one full-scope run (fan-out per command, per-command timeout, never aborts).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string", "default": "."},
                "timeout": {"type": "number", "default": CHAIN_CMD_TIMEOUT},
                "budget": {"type": "number", "default": 300},
                "only": {"type": "string", "description": "Comma-separated command subset"},
            },
            "additionalProperties": True,
        },
    })
    tools.append({
        "name": "ssb_twin_evolution",
        "description": "[BEAST M3] Clone the tool against itself: twin brains scan the same targets, diff, merge union without duplicates into a combined build.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "rounds": {"type": "number", "default": 2},
                "workspace": {"type": "string", "default": "."},
                "target": {"type": "string", "default": ""},
            },
            "additionalProperties": True,
        },
    })
    return tools


def call_mcp_tool(core: BeastCore, name: str, arguments: dict) -> dict:
    """Route an MCP tools/call to a beast handler, an original-endpoint proxy,
    or a command fan-out. Always returns an MCP-style content envelope."""
    name = str(name or "").strip()
    arguments = arguments if isinstance(arguments, dict) else {}
    try:
        # 1) Beast endpoint tools
        if name in BEAST_HANDLERS:
            query = {k: ("" if v is None else v) for k, v in arguments.items()}
            result = BEAST_HANDLERS[name](core, query)
            return {"content": [{"type": "text", "text": _json_dumps(result, indent=2)}]}
        # 2) Original /api/* tools -> byte-level proxy to the internal server
        if name.startswith("api_"):
            route_name = name[len("api_"):]
            route = next((r for r in ORIGINAL_API_ROUTES if r["name"] == route_name), None)
            if route is None:
                return {"isError": True, "content": [{"type": "text",
                        "text": f"unknown original api tool {name}"}]}
            if route["path"] == "/events":
                return {"isError": True, "content": [{"type": "text",
                        "text": "/events is a server-sent-events stream; connect directly"}]}
            query = {k: v for k, v in arguments.items() if v not in (None, "")}
            status, content_type, body = internal_get(core, route["path"], query)
            text = body.decode("utf-8", errors="replace")
            if len(text) > 400000:
                text = text[:400000] + "\n... [truncated]"
            header = f"proxied GET {route['path']} -> status {status} ({content_type})"
            return {"content": [{"type": "text", "text": f"{header}\n{text}"}]}
        # 3) Command fan-out / M3 tools
        if name == "ssb_command":
            argv = arguments.get("argv")
            if not argv and arguments.get("command"):
                argv = shlex.split(str(arguments["command"]))
            if not argv:
                return {"isError": True, "content": [{"type": "text",
                        "text": "ssb_command requires argv (array) or command (string)"}]}
            timeout = arguments.get("timeout")
            result = core.dispatcher.fanout([str(a) for a in argv],
                                            timeout=float(timeout) if timeout else None)
            return {"content": [{"type": "text", "text": _json_dumps(result, indent=2)}]}
        if name == "ssb_chain_all":
            result = run_chain_all(
                workspace=str(arguments.get("workspace") or core.workspace),
                timeout=float(arguments.get("timeout") or CHAIN_CMD_TIMEOUT),
                budget_s=float(arguments.get("budget") or 300),
                only=[c for c in str(arguments.get("only") or "").split(",") if c] or None,
                dispatcher=core.dispatcher, brain=core.brain)
            return {"content": [{"type": "text", "text": _json_dumps(result, indent=2)}]}
        if name == "ssb_twin_evolution":
            result = run_twin_evolution(
                rounds=max(1, min(20, int(float(arguments.get("rounds") or 2)))),
                workspace=str(arguments.get("workspace") or core.workspace),
                target=str(arguments.get("target") or ""))
            return {"content": [{"type": "text", "text": _json_dumps(result, indent=2)}]}
        return {"isError": True, "content": [{"type": "text",
                "text": f"unknown tool {name!r}; see /beast/api/mcp/tools/list"}]}
    except Exception as exc:
        return {"isError": True, "content": [{"type": "text",
                "text": f"tool {name} failed: {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1500:]}"}]}


# ---------------------------------------------------------------------------
# M1: BeastFrontServer — Beast API locally + byte-transparent reverse proxy
# ---------------------------------------------------------------------------
class BeastFrontHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class BeastFrontHandler(http.server.BaseHTTPRequestHandler):
    server_version = f"SSBBeast/{BEAST_VERSION}"
    protocol_version = "HTTP/1.1"

    # -- plumbing ------------------------------------------------------------
    @property
    def core(self) -> BeastCore:
        return self.server.core  # type: ignore[attr-defined]

    def log_message(self, fmt, *args):
        try:
            sys.stderr.write(f"[ssb-beast:http] {self.client_address[0]} {fmt % args}\n")
        except Exception:
            pass

    def _send_raw_text(self, text: str, status: int = 200) -> None:
        body = text.encode("utf-8", errors="replace")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _send_raw_json(self, obj, status: int = 200) -> None:
        """ADDITIVE v15: C-speed serialization for FULL RAW dumps — no per-node
        Python sanitizer walk (that walk turns a 5k-event brain into minutes
        of GIL-burning recursion). Data is plain dicts/lists from the scanners."""
        try:
            body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8", errors="replace")
        except Exception:
            body = b'{"ok": false, "error": "json encode failure"}'
            status = 500
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _send_json(self, obj, status: int = 200) -> None:
        try:
            body = _json_dumps(obj, indent=2).encode("utf-8", errors="replace")
        except Exception:
            body = b'{"ok": false, "error": "json encode failure"}'
            status = 500
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _send_html(self, html: str, status: int = 200) -> None:
        body = html.encode("utf-8", errors="replace")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _error_json(self, status: int, message: str) -> None:
        try:
            self._send_json({"ok": False, "error": message,
                             "trace": traceback.format_exc()[-1500:]}, status=status)
        except Exception:
            pass

    # -- verbs ---------------------------------------------------------------
    def do_GET(self):
        self._route()

    def do_POST(self):
        self._route()

    def do_PUT(self):
        self._route()

    def do_DELETE(self):
        self._route()

    def do_PATCH(self):
        self._route()

    def do_HEAD(self):
        self._route()

    def do_OPTIONS(self):
        self._route()

    # -- routing -------------------------------------------------------------
    def _route(self) -> None:
        try:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path.startswith("/beast/api/") or parsed.path in ("/beast", "/beast/"):
                self._handle_beast(parsed)
            elif parsed.path in ("/v1/messages", "/v1/chat/completions"):
                # Kimi Code / Anthropic-style messaging entry points.
                self._handle_messaging(parsed)
            elif parsed.path in ("/", "/index.html"):
                # Beast GodScope GUI is the face of port 8787 (original GUI at /legacy).
                self._handle_beast_gui()
            elif parsed.path == "/legacy" or parsed.path.startswith("/legacy/"):
                # Original Galaxy Brain GUI + assets, byte-transparent via proxy.
                self._proxy_to_internal(rewrite_prefix="/legacy")
            else:
                self._proxy_to_internal()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            self._error_json(500, f"beast route error: {type(exc).__name__}: {exc}")

    def _handle_messaging(self, parsed) -> None:
        if self.command != "POST":
            return self._error_json(405, "messaging endpoints require POST")
        try:
            raw = self._read_body()
            payload = json.loads(raw.decode("utf-8", errors="replace")) if raw else {}
            if not isinstance(payload, dict):
                payload = {}
        except Exception as exc:
            return self._error_json(400, f"invalid JSON body: {exc}")
        if parsed.path == "/v1/messages":
            return self._send_json(messaging_v1_messages(self.core, payload))
        return self._send_json(messaging_chat_completions(self.core, payload))

    def _handle_beast_gui(self) -> None:
        candidates = []
        env_gui = os.environ.get("SSB_BEAST_GUI", "").strip()
        if env_gui:
            candidates.append(Path(env_gui))
        candidates += [
            SSB_DIR / "beast_gui.html",
            Path(__file__).resolve().parent / "beast_gui.html",
            Path("/mnt/agents/output/ssb/project/beast_gui.html"),
        ]
        for cand in candidates:
            try:
                if cand.exists() and cand.stat().st_size > 0:
                    body = cand.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(body)
                    return
            except Exception:
                continue
        # GUI file not deployed yet — fall back to the endpoint index.
        self._handle_beast_index()

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > 0:
            return self.rfile.read(length)
        return b""

    def _query_dict(self, parsed) -> dict:
        out = {}
        for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
            out[k] = v
        return out

    def _handle_beast(self, parsed) -> None:
        path = parsed.path
        query = self._query_dict(parsed)
        # JSON request bodies (POST/PUT/PATCH) merge into params; URL wins.
        if self.command in ("POST", "PUT", "PATCH") and path not in ("/beast/api/mcp/tools/call",):
            try:
                raw = self._read_body()
                if raw:
                    body_obj = json.loads(raw.decode("utf-8", errors="replace"))
                    if isinstance(body_obj, dict):
                        query = {**{k: v for k, v in body_obj.items()}, **query}
            except Exception:
                pass
        if path in ("/beast", "/beast/"):
            return self._handle_beast_index()
        try:
            if path == "/beast/api/health":
                return self._send_json(beast_health(self.core, query))
            if path == "/beast/api/viz-data":
                cached = beast_viz_data_cached(self.core, query)
                if cached is not None:
                    return self._send_raw_text(cached)
                return self._send_json(beast_viz_data(self.core, query))
            if path == "/beast/api/knowledge-surface":
                return self._send_json(beast_knowledge_surface(self.core, query))
            if path == "/beast/api/process-flow":
                return self._send_json(beast_process_flow(self.core, query))
            if path == "/beast/api/node":
                return self._send_json(beast_node_detail(self.core, query))
            if path == "/beast/api/godscope":
                return self._send_json(beast_godscope(self.core, query))
            if path == "/beast/api/godscope/endpoints":
                return self._send_json(beast_godscope_endpoints(self.core, query))
            if path == "/beast/api/combo":
                return self._send_json(beast_combo(self.core, query))
            if path == "/beast/api/chain-all":
                return self._send_json(beast_chain_all(self.core, query))
            if path == "/beast/api/twin":
                return self._send_json(beast_twin(self.core, query))
            if path == "/beast/api/openclaw/methods":
                return self._send_json(beast_openclaw_methods(self.core, query))
            if path == "/beast/api/hermes/methods":
                return self._send_json(beast_hermes_methods(self.core, query))
            if path == "/beast/api/openclaw/call":
                return self._send_json(beast_openclaw_call(self.core, query))
            if path == "/beast/api/hermes/call":
                return self._send_json(beast_hermes_call(self.core, query))
            if path == "/beast/api/hermes/status":
                return self._send_json(beast_hermes_status(self.core, query))
            if path == "/beast/api/hermes/autonomy":
                return self._send_json(beast_hermes_autonomy(self.core, query))
            # ---- OS LAYER + FULL RAW (ADDITIVE v15) ----
            if path == "/beast/api/os/exec":
                if not os_exec_authorized(self):
                    return self._error_json(403, "exec key required (x-ssb-exec-key)")
                if self.command == "GET":
                    # GET lane (proven-framing): /beast/api/os/exec?cmd=...&timeout=20&cwd=/
                    payload = {"cmd": str(query.get("cmd", "")),
                               "timeout": str(query.get("timeout", "30")),
                               "cwd": str(query.get("cwd", "/"))}
                    return self._send_json(beast_os_exec(self.core, payload))
                if self.command != "POST":
                    return self._error_json(405, "GET or POST required")
                raw = self._read_body()
                try:
                    payload = json.loads(raw.decode("utf-8", errors="replace")) if raw else {}
                except Exception:
                    payload = {}
                return self._send_json(beast_os_exec(self.core, payload))
            if path == "/beast/api/os/write":
                if not os_exec_authorized(self):
                    return self._error_json(403, "exec key required (x-ssb-exec-key)")
                if self.command == "GET":
                    payload = {"path": str(query.get("path", "")),
                               "content": str(query.get("content", ""))}
                    return self._send_json(beast_os_write(self.core, payload))
                if self.command != "POST":
                    return self._error_json(405, "GET or POST required")
                raw = self._read_body()
                try:
                    payload = json.loads(raw.decode("utf-8", errors="replace")) if raw else {}
                except Exception:
                    payload = {}
                return self._send_json(beast_os_write(self.core, payload))
            if path == "/beast/api/os/read":
                return self._send_json(beast_os_read(self.core, query))
            if path == "/beast/api/os/ps":
                return self._send_json(beast_os_ps(self.core, query))
            if path == "/beast/api/os/tools":
                return self._send_json(beast_os_tools(self.core, query))
            if path == "/beast/api/raw/brain":
                return self._send_raw_json(beast_raw_brain(self.core, query))
            if path == "/beast/api/raw/scans":
                return self._send_raw_json(beast_raw_scans(self.core, query))
            if path == "/beast/api/raw/logs":
                return self._send_json(beast_raw_logs(self.core, query))
            if path == "/beast/api/raw/code":
                return self._send_json(beast_raw_code(self.core, query))
            if path == "/beast/api/messaging/status":
                return self._send_json(beast_messaging_status(self.core, query))
            if path == "/beast/api/messaging/feed":
                return self._send_json(beast_messaging_feed(self.core, query))
            if path == "/beast/api/mcp/tools/list":
                return self._send_json({"tools": build_mcp_tools(self.core)})
            if path == "/beast/api/mcp/tools/call":
                payload = {}
                if self.command in ("POST", "PUT", "PATCH"):
                    raw = self._read_body()
                    if raw:
                        payload = json.loads(raw.decode("utf-8", errors="replace"))
                if not payload:
                    payload = {"name": query.get("name", ""),
                               "arguments": query.get("arguments", {})}
                    if isinstance(payload["arguments"], str):
                        try:
                            payload["arguments"] = json.loads(payload["arguments"] or "{}")
                        except Exception:
                            payload["arguments"] = {}
                result = call_mcp_tool(self.core, payload.get("name", ""),
                                       payload.get("arguments", {}))
                return self._send_json(result)
            return self._send_json({"ok": False, "error": f"unknown beast endpoint {path}",
                                    "known": [r["path"] for r in BEAST_API_ROUTES]}, status=404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            self._error_json(500, f"beast endpoint error: {type(exc).__name__}: {exc}")

    def _handle_beast_index(self) -> None:
        core = self.core
        rows = "".join(
            f'<tr><td><a href="{r["path"]}">{r["path"]}</a></td><td>{r["description"]}</td></tr>'
            for r in BEAST_API_ROUTES if "{" not in r["path"])
        html = f"""<!doctype html><html><head><meta charset="utf-8"><title>SSB BEAST</title>
<style>body{{background:#05070f;color:#7dffca;font-family:monospace;padding:24px}}
a{{color:#42f8ff}}td{{padding:2px 10px;vertical-align:top}}h1{{color:#ff1765}}</style></head>
<body><h1>SSB BEAST LAYER {BEAST_VERSION}</h1>
<p>Original Galaxy Brain GUI is served byte-transparently at <a href="/">/</a>
(internal original brain: http://{core.internal_host}:{core.internal_port}/).
All non-<code>/beast</code> paths proxy to it unmodified.</p>
<table>{rows}</table>
<p>MCP: <a href="/beast/api/mcp/tools/list">/beast/api/mcp/tools/list</a> |
POST /beast/api/mcp/tools/call {{"name":..., "arguments":{{...}}}}</p>
</body></html>"""
        self._send_html(html)

    # -- byte-transparent reverse proxy --------------------------------------
    def _proxy_to_internal(self, rewrite_prefix: str = "") -> None:
        core = self.core
        parsed = urllib.parse.urlparse(self.path)
        upstream_path = self.path
        if rewrite_prefix and parsed.path.startswith(rewrite_prefix):
            stripped = parsed.path[len(rewrite_prefix):] or "/"
            upstream_path = stripped + (("?" + parsed.query) if parsed.query else "")
        is_sse = parsed.path == "/events"
        if is_sse:
            timeout = None  # long-lived stream
        elif parsed.path.startswith(SLOW_PROXY_PREFIXES):
            timeout = PROXY_SLOW_TIMEOUT
        else:
            timeout = PROXY_TIMEOUT
        body = self._read_body() if self.command in ("POST", "PUT", "PATCH") else None
        headers = {}
        for key, value in self.headers.items():
            lk = key.lower()
            if lk in HOP_BY_HOP or lk == "host":
                continue
            headers[key] = value
        headers["Host"] = f"{core.internal_host}:{core.internal_port}"
        conn = http.client.HTTPConnection(core.internal_host, core.internal_port,
                                          timeout=timeout if timeout else 87600)
        try:
            conn.request(self.command, upstream_path, body=body, headers=headers)
            resp = conn.getresponse()
        except Exception as exc:
            conn.close()
            return self._error_json(502, f"proxy to internal brain failed: {type(exc).__name__}: {exc}")
        try:
            if is_sse:
                # Streamed pass-through (close-delimited): preserves the SSE feed.
                self.send_response(resp.status, resp.reason)
                for key, value in resp.getheaders():
                    if key.lower() in HOP_BY_HOP or key.lower() == "content-length":
                        continue
                    self.send_header(key, value)
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                while True:
                    try:
                        chunk = resp.read1(8192)
                    except (OSError, http.client.IncompleteRead):
                        break
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        break
                return
            payload = resp.read()
            self.send_response(resp.status, resp.reason)
            sent_len = False
            for key, value in resp.getheaders():
                lk = key.lower()
                if lk in HOP_BY_HOP or lk == "content-length":
                    continue
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            sent_len = True
            self.end_headers()
            if self.command != "HEAD":
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass
        except http.client.IncompleteRead as exc:
            try:
                partial = exc.partial or b""
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(partial)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(partial)
            except Exception:
                pass
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            self._error_json(502, f"proxy stream error: {type(exc).__name__}: {exc}")
        finally:
            try:
                conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# M1: boot the whole stack (shared brain + internal original server + front)
# ---------------------------------------------------------------------------
def wait_for_port(host: str, port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, int(port)), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def boot_brain_stack(host: str = "127.0.0.1", port: int = 8787,
                     internal_port: int = 8791, workspace: str = ".",
                     roots: list | None = None, poll: float = 2.0,
                     max_watch_files: int = 12000, max_events: int = 20000,
                     open_browser: bool = False) -> int:
    _log(f"booting SSB BEAST LAYER {BEAST_VERSION} (M1+M3)")
    module = MonolithLoader.load(verbose=True)
    root_list = []
    for candidate in [workspace] + list(roots or []):
        if candidate and str(candidate) not in root_list:
            root_list.append(str(candidate))
    _log(f"creating shared SSBGalaxyBrain roots={root_list}")
    brain = module.SSBGalaxyBrain(
        roots=root_list,
        max_events=int(max_events or 20000),
        poll_interval=float(poll or 2.0),
        max_watch_files=int(max_watch_files or 12000),
    )
    # NOTE: no synchronous load_existing_events() here — the original serve()
    # replays the event log inside its poller thread; doing it inline would
    # stall boot on large logs (original behavior preserved, faster boot).
    core = BeastCore(module, brain, host, int(port), host, int(internal_port), workspace)
    try:
        _patch_secret_auditor(brain)  # ADDITIVE v14: break upstream audit recursion
    except Exception as exc:
        _log(f"auditor patch: {type(exc).__name__}: {exc}")
    try:
        load_memory_baseline(core)   # openclaw/hermes skills+memories at baseline
    except Exception as exc:
        _log(f"memory baseline: {type(exc).__name__}: {exc}")
    try:
        _messaging_init(core)        # provider router + bridge + offline engine
    except Exception as exc:
        _log(f"messaging init: {type(exc).__name__}: {exc}")
    try:
        hermes_start(core)           # ADDITIVE v14: hermes autonomy loop (self-gates on env)
    except Exception as exc:
        _log(f"hermes start: {type(exc).__name__}: {exc}")
    try:
        _exec_key()                  # ADDITIVE v15: create exec key eagerly (portal reads same file)
    except Exception as exc:
        _log(f"exec key: {type(exc).__name__}: {exc}")

    def _serve_internal():
        try:
            # UNMODIFIED original brain server on the internal port.
            brain.serve(host=host, port=int(internal_port), open_browser=False)
        except Exception as exc:
            _log(f"internal brain serve() exited: {type(exc).__name__}: {exc}")

    threading.Thread(target=_serve_internal, name="ssb-internal-brain", daemon=True).start()
    _log(f"waiting for internal original brain on {host}:{internal_port} ...")
    if not wait_for_port(host, int(internal_port), timeout=25.0):
        _log("WARNING: internal brain port did not open in time; proxy will 502 until it does")
    else:
        _log(f"internal original brain live: http://{host}:{internal_port}/")

    server = BeastFrontHTTPServer((host, int(port)), BeastFrontHandler)
    server.core = core  # type: ignore[attr-defined]
    _log(f"beast front server (proxy + beast api): http://{host}:{port}/")
    _log(f"beast api health: http://{host}:{port}/beast/api/health")
    _log(f"godscope endpoints: http://{host}:{port}/beast/api/godscope/endpoints")
    if open_browser:
        try:
            import webbrowser
            webbrowser.open(f"http://{host}:{port}/")
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _log("keyboard interrupt: shutting down beast layer")
    finally:
        try:
            brain.stop_event.set()
        except Exception:
            pass
        try:
            server.server_close()
        except Exception:
            pass
    return 0


# ---------------------------------------------------------------------------
# M1/M3: CLI
# ---------------------------------------------------------------------------
BEAST_COMMANDS = ("brain", "combo", "chain-all", "godscope", "twin")


def build_beast_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ssb_beast.py",
        description="SSB BEAST LAYER — additive fan-out wrapper around the SSB monolith (M1+M3)")
    parser.add_argument("--version", action="store_true", help="print beast version and exit")
    sub = parser.add_subparsers(dest="beast_command")

    b = sub.add_parser("brain", help="Boot internal original brain + beast front server (proxy + beast api)")
    b.add_argument("--host", default="127.0.0.1")
    b.add_argument("--port", type=int, default=8787, help="public beast front port")
    b.add_argument("--internal-port", type=int, default=8791, help="internal original brain port")
    b.add_argument("--workspace", default=".")
    b.add_argument("--root", action="append", default=[], help="extra watched root (repeatable)")
    b.add_argument("--watch", action="append", default=[], help="extra watched root alias (repeatable)")
    b.add_argument("--poll", type=float, default=2.0)
    b.add_argument("--max-watch-files", type=int, default=12000)
    b.add_argument("--max-events", type=int, default=20000)
    b.add_argument("--open", action="store_true", help="open browser after boot")

    c = sub.add_parser("combo", help="Fan one command out: normal+broad+kernel concurrently")
    c.add_argument("--timeout", type=float, default=LANE_TIMEOUT, help="per-lane timeout seconds")
    c.add_argument("cmd", help="original command name, e.g. health")
    c.add_argument("cmd_args", nargs=argparse.REMAINDER, help="arguments passed to the original command")

    ca = sub.add_parser("chain-all", help="M3: chain every CLI command in one full-scope run")
    ca.add_argument("--workspace", default=".")
    ca.add_argument("--timeout", type=float, default=CHAIN_CMD_TIMEOUT, help="per-command timeout seconds")
    ca.add_argument("--budget", type=float, default=300, help="overall seconds budget (0 = no cap)")
    ca.add_argument("--only", default="", help="comma-separated command subset")
    ca.add_argument("--include", default="", help="comma-separated commands to un-skip")
    ca.add_argument("--skip", default="", help="comma-separated extra commands to skip")
    ca.add_argument("--out", default="", help="write full JSON report here")

    g = sub.add_parser("godscope", help="Print the full endpoint/command manifest")
    g.add_argument("--host", default="127.0.0.1")
    g.add_argument("--port", type=int, default=0, help="fetch live manifest from a running beast server")
    g.add_argument("--internal-port", type=int, default=8791)
    g.add_argument("--workspace", default=".")

    t = sub.add_parser("twin", help="M3: twin evolution — parallel twin scan, diff, merge union")
    t.add_argument("--rounds", type=int, default=2)
    t.add_argument("--workspace", default=".")
    t.add_argument("--target", default="")
    t.add_argument("--scan-timeout", type=float, default=60.0)
    t.add_argument("--out", default="", help="write merged combined build JSON here")
    return parser


def _csv(value: str) -> list:
    return [v.strip() for v in str(value or "").split(",") if v.strip()]


def main(argv: list | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Pass-through of ANY original command with fan-out (additive wrapper).
    if argv and argv[0] not in BEAST_COMMANDS and argv[0] not in ("-h", "--help", "--version"):
        dispatcher = FanOutDispatcher()
        if dispatcher.solo:
            return dispatcher.run_solo_original(argv)
        envelope = dispatcher.fanout(argv)
        print(_json_dumps(envelope, indent=2))
        return 0 if envelope.get("ok") else 1

    parser = build_beast_parser()
    args = parser.parse_args(argv)
    if getattr(args, "version", False):
        print(BEAST_VERSION)
        return 0
    command = getattr(args, "beast_command", None)
    if not command:
        parser.print_help()
        return 0

    if command == "brain":
        return boot_brain_stack(
            host=args.host, port=args.port, internal_port=args.internal_port,
            workspace=args.workspace, roots=list(args.root or []) + list(args.watch or []),
            poll=args.poll, max_watch_files=args.max_watch_files,
            max_events=args.max_events, open_browser=bool(args.open))

    if command == "combo":
        combo_argv = [args.cmd] + [a for a in (args.cmd_args or []) if a != "--"]
        dispatcher = FanOutDispatcher()
        envelope = dispatcher.fanout(combo_argv, timeout=args.timeout)
        print(_json_dumps(envelope, indent=2))
        return 0 if envelope.get("ok") else 1

    if command == "chain-all":
        report = run_chain_all(
            workspace=args.workspace, timeout=args.timeout,
            include=_csv(args.include), skip=_csv(args.skip), only=_csv(args.only) or None,
            budget_s=float(args.budget or 0))
        rendered = _json_dumps(report, indent=2)
        if args.out:
            Path(args.out).expanduser().write_text(rendered, encoding="utf-8")
            _log(f"chain-all report written to {args.out}")
        print(rendered)
        return 0 if report.get("ok") else 1

    if command == "godscope":
        if args.port:
            try:
                conn = http.client.HTTPConnection(args.host, int(args.port), timeout=15)
                conn.request("GET", "/beast/api/godscope/endpoints")
                resp = conn.getresponse()
                body = resp.read().decode("utf-8", errors="replace")
                print(body)
                return 0 if resp.status == 200 else 1
            except Exception as exc:
                print(_json_dumps({"ok": False,
                                   "error": f"live godscope fetch failed: {type(exc).__name__}: {exc}"}))
                return 1
        module = MonolithLoader.load()
        core = BeastCore(module, None, args.host, 8787, args.host, args.internal_port, args.workspace)
        print(_json_dumps(beast_godscope_endpoints(core, {}), indent=2))
        return 0

    if command == "twin":
        report = run_twin_evolution(
            rounds=max(1, int(args.rounds or 2)), workspace=args.workspace,
            target=args.target, merge_out=args.out, scan_timeout=args.scan_timeout)
        print(_json_dumps(report, indent=2))
        return 0 if report.get("ok") else 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
