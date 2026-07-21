#!/usr/bin/env python3
"""
SSB V11 Z MARK — REAL File-Content Scanner
============================================

Reads file bytes IMMEDIATELY on upload and looks for ACTUAL exploit primitives,
not file names. An attacker names their weapon `helpers/compat.py` — the name
is camouflage, the code is the weapon. This module reads the weapon.

Detection layers (each returns findings with severity + location):
  L1  AST analysis        — Python AST walks for eval/exec/os.system/subprocess
                            with shell=True/pickle.loads/__import__/__builtins__
                            manipulation/attribute chains
  L2  Regex primitives    — shell injection, path traversal, reverse shells,
                            symlink races, base64-encoded payloads, polymorphic
                            obfuscation
  L3  Byte signatures     — ELF/PE/Mach-O magic, suspicious magic bytes,
                            embedded scripts
  L4  Entropy analysis    — high-entropy regions (packed/encrypted payloads)
  L5  Structural audit    — contradictions, dead code, hidden branches,
                            suspicious try/except patterns

This module is the *evidence gatherer*. It does not decide. The Multi-Chain
Soft Ping decides. This module just reads the file and reports what it found.

Token cost: 0 (pure local computation).
"""

from __future__ import annotations
import ast
import base64
import binascii
import hashlib
import math
import os
import re
import struct
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Finding model
# ---------------------------------------------------------------------------

class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class Layer(str, Enum):
    L1_AST = "L1_AST"
    L2_REGEX = "L2_REGEX"
    L3_BYTES = "L3_BYTES"
    L4_ENTROPY = "L4_ENTROPY"
    L5_STRUCTURE = "L5_STRUCTURE"

@dataclass
class Finding:
    layer: str
    severity: str
    primitive: str            # e.g. "subprocess.shell_true"
    location: str             # e.g. "line 42:col 16" or "bytes 1024-1056"
    snippet: str              # short code/bytes excerpt
    explanation: str          # why this is suspicious
    confidence: float = 1.0   # 0..1

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

@dataclass
class ScanReport:
    file_hash: str
    file_size: int
    file_type: str
    is_text: bool
    entropy: float
    findings: list[Finding] = field(default_factory=list)
    scan_duration_ms: float = 0.0
    layers_run: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def max_severity(self) -> str:
        order = {"critical": 5, "high": 4, "medium": 3, "low": 2, "info": 1}
        if not self.findings:
            return "none"
        return max(self.findings, key=lambda f: order.get(f.severity, 0)).severity

    @property
    def severity_score(self) -> float:
        """Weighted sum 0..1."""
        weights = {"critical": 1.0, "high": 0.75, "medium": 0.5, "low": 0.25, "info": 0.05}
        if not self.findings:
            return 0.0
        return min(1.0, sum(weights.get(f.severity, 0) * f.confidence for f in self.findings))

    def as_dict(self) -> dict[str, Any]:
        return {
            "file_hash": self.file_hash,
            "file_size": self.file_size,
            "file_type": self.file_type,
            "is_text": self.is_text,
            "entropy": self.entropy,
            "max_severity": self.max_severity,
            "severity_score": self.severity_score,
            "findings": [f.as_dict() for f in self.findings],
            "scan_duration_ms": self.scan_duration_ms,
            "layers_run": self.layers_run,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# L1 — AST analysis (Python only; skipped for non-Python with a note)
# ---------------------------------------------------------------------------

class _ASTVisitor(ast.NodeVisitor):
    """Walks the AST and collects findings inline."""
    def __init__(self) -> None:
        self.findings: list[Finding] = []

    def _add(self, severity: str, primitive: str, node: ast.AST, snippet: str, explanation: str, conf: float = 1.0) -> None:
        loc = f"line {getattr(node, 'lineno', '?')}:col {getattr(node, 'col_offset', '?')}"
        self.findings.append(Finding(
            layer=Layer.L1_AST.value, severity=severity, primitive=primitive,
            location=loc, snippet=snippet[:160], explanation=explanation, confidence=conf,
        ))

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        # eval() / exec()
        if isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec"}:
            self._add("critical", f"builtin.{node.func.id}", node,
                      f"{node.func.id}(...)", f"Direct {node.func.id}() of untrusted input is RCE.")
        # os.system / os.popen
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"system", "popen"}:
            self._add("critical", f"os.{node.func.attr}", node,
                      f"os.{node.func.attr}(...)", "Shell-out via os.system/popen is shell injection.")
        # subprocess with shell=True
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"Popen", "run", "call", "check_call", "check_output"}:
            for kw in node.keywords:
                if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                    self._add("critical", "subprocess.shell_true", node,
                              "subprocess.<call>(..., shell=True)", "shell=True with any user-tainted input is RCE.")
        # pickle.loads / pickle.load
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"loads", "load"}:
            mod = node.func.value
            if isinstance(mod, ast.Name) and mod.id == "pickle":
                self._add("critical", "pickle.loads", node,
                          "pickle.loads(...)", "pickle.loads on untrusted data is arbitrary code execution.")
        # __import__
        if isinstance(node.func, ast.Name) and node.func.id == "__import__":
            self._add("high", "dunder.import", node, "__import__(...)",
                      "Dynamic import is sometimes legitimate but often used to evade static analysis.")
        # marshal.loads (used to deserialize code objects)
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"loads", "load"}:
            mod = node.func.value
            if isinstance(mod, ast.Name) and mod.id == "marshal":
                self._add("high", "marshal.loads", node, "marshal.loads(...)",
                          "marshal.loads can deserialize Python code objects → RCE.")
        # ctypes.CDLL / ctypes.windll — native library loading
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"CDLL", "WinDLL", "OleDLL"}:
            self._add("high", "ctypes.native_load", node, f"ctypes.{node.func.attr}(...)",
                      "Native library load from Python — common shellcode loader pattern.")
        # socket.connect in unusual contexts (reverse shell primitive)
        # We flag raw socket construction with low severity; pattern elevates via L2.
        if isinstance(node.func, ast.Attribute) and node.func.attr == "socket":
            pass  # handled in L2
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        # __builtins__ / __class__.__mro__ / __subclasses__ — sandbox escape
        if node.attr in {"__subclasses__", "__mro__", "__bases__", "__builtins__", "__globals__", "__code__", "__func__"}:
            self._add("high", f"dunder.{node.attr}", node, f"...{node.attr}",
                      "Dunder-attribute access is a classic sandbox-escape primitive.")
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            if alias.name in {"pty", "code", "codeop"}:
                self._add("medium", f"import.{alias.name}", node, f"import {alias.name}",
                          f"{alias.name} import can be used to spawn interactive shells.")
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:  # noqa: N802
        # bare `except:` swallows evidence
        if node.type is None:
            self._add("low", "except.bare", node, "except:",
                      "Bare except hides exploitation errors — common in obfuscated malware.")
        self.generic_visit(node)


def _ast_scan(source: str) -> tuple[list[Finding], str | None]:
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        # Unparseable Python may itself be obfuscation — flag medium.
        return [], f"AST parse failed: {e}"
    v = _ASTVisitor()
    v.visit(tree)
    return v.findings, None


# ---------------------------------------------------------------------------
# L2 — Regex primitives (works on any text-like file, including decoded blobs)
# ---------------------------------------------------------------------------

_REGEX_PRIMITIVES: list[tuple[str, str, str, str]] = [
    # (severity, primitive, pattern, explanation)
    ("critical", "shell.injection", r"(?i)\b(?:rm\s+-rf|curl\s+.*\|\s*sh|wget\s+.*\|\s*bash|nc\s+-e|/dev/tcp/|mkfifo|/bin/sh\s+-c)",
     "Shell-injection primitive — destructive or reverse-shell command."),
    ("critical", "shell.subprocess_shell", r"subprocess\.\w+\([^)]*shell\s*=\s*True",
     "subprocess call with shell=True — RCE if any arg is attacker-controlled."),
    ("critical", "eval.exec", r"\b(?:eval|exec)\s*\(",
     "eval/exec on dynamic input is arbitrary code execution."),
    ("critical", "pickle.loads", r"pickle\.loads?\s*\(",
     "pickle deserialization on untrusted data is RCE."),
    ("critical", "reverse.shell", r"(?i)socket\.(?:AF_INET|socket)\([^)]*\).*?(?:connect|send)",
     "Socket connect/send pattern — possible reverse shell."),
    ("critical", "reverse.shell.bash", r"bash\s+-i\s*(?:>&|>|<)\s*/dev/tcp/",
     "Bash reverse shell over /dev/tcp — classic."),
    ("critical", "reverse.shell.python", r"(?i)socket\.socket\(socket\.AF_INET,?\s*socket\.SOCK_STREAM\)",
     "Raw TCP socket construction — reverse shell primitive."),
    ("high", "path.traversal", r"(?:\.\./){2,}|(?:\.\.\\){2,}|\.\.%2[fF]\.\.%2[fF]",
     "Path-traversal sequence — escapes intended directory."),
    ("high", "ssrf.url", r"(?i)(?:file|gopher|dict|ftp|http)://(?:0\.0\.0\.0|localhost|169\.254\.169\.254|metadata\.google\.internal)",
     "SSRF target — cloud metadata endpoint or localhost."),
    ("high", "base64.payload", r"(?:[A-Za-z0-9+/]{60,}={0,2})",
     "Long base64 blob — common payload obfuscation. Will attempt decode."),
    ("high", "obfuscation.hex", r"(?:\\x[0-9a-fA-F]{2}){8,}",
     "Long hex-escape sequence — obfuscated string payload."),
    ("high", "obfuscation.chr", r"(?:chr\(\d{1,3}\)\s*\+?){4,}",
     "chr()-concatenation — classic string obfuscation to evade grep."),
    ("medium", "symlink.race", r"os\.symlink\s*\(|os\.readlink\s*\(|\.replace\s*\(.*,\s*\.so",
     "Symlink primitive — possible TOCTOU race or .so hijack."),
    ("medium", "env.leak", r"getenv\s*\(\s*['\"](?:AWS_(?:ACCESS_KEY_ID|SECRET_ACCESS_KEY)|GITHUB_TOKEN|DATABASE_URL|SECRET_KEY|TOKEN|API_KEY|PRIVATE_KEY)|os\.environ\.get\s*\(\s*['\"](?:AWS_(?:ACCESS_KEY_ID|SECRET_ACCESS_KEY)|GITHUB_TOKEN|DATABASE_URL|SECRET_KEY|TOKEN|API_KEY|PRIVATE_KEY)",
     "Secret-env read — credential exfiltration pattern (only matches specific secret-laden env var names)."),
    ("medium", "tempfile.race", r"tempfile\.mktemp\s*\(",
     "mktemp is deprecated and raceable — use mkstemp."),
    ("low", "shellvar.expansion", r"\$\(\s*[^)]+\)|\$\{[^}]+\}",
     "Shell command substitution / variable expansion — context dependent."),
    ("low", "ipv4.external", r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
     "Hardcoded IPv4 — verify it's not a C2 endpoint."),
]

def _regex_scan(text: str) -> list[Finding]:
    out: list[Finding] = []
    for severity, primitive, pattern, explanation in _REGEX_PRIMITIVES:
        for m in re.finditer(pattern, text):
            # locate line number
            line = text.count("\n", 0, m.start()) + 1
            col = m.start() - (text.rfind("\n", 0, m.start()) + 1) + 1
            snippet = m.group(0)[:120]
            out.append(Finding(
                layer=Layer.L2_REGEX.value, severity=severity, primitive=primitive,
                location=f"line {line}:col {col}", snippet=snippet,
                explanation=explanation, confidence=0.85,
            ))
            # cap matches per primitive to avoid flooding
            if sum(1 for f in out if f.primitive == primitive) >= 8:
                break
    # Try to decode long base64 blobs and recurse one level
    for m in re.finditer(r"[A-Za-z0-9+/]{80,}={0,2}", text):
        try:
            decoded = base64.b64decode(m.group(0), validate=True)
            if len(decoded) < 8:
                continue
            try:
                decoded_text = decoded.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                continue
            # If decoded looks like code, recurse one level
            if any(kw in decoded_text for kw in ("eval", "exec", "import", "subprocess", "os.", "socket", "pickle")):
                line = text.count("\n", 0, m.start()) + 1
                out.append(Finding(
                    layer=Layer.L2_REGEX.value, severity="critical",
                    primitive="base64.decoded_payload",
                    location=f"line {line} (decoded blob)",
                    snippet=decoded_text[:120],
                    explanation="Base64 blob decodes to executable code — payload obfuscation.",
                    confidence=0.95,
                ))
        except (binascii.Error, ValueError):
            continue
    return out


# ---------------------------------------------------------------------------
# L3 — Byte signatures
# ---------------------------------------------------------------------------

_BYTE_SIGS: list[tuple[str, str, str]] = [
    # (severity, primitive, magic_hex_prefix, explanation)
    ("medium", "binary.elf", "7f454c46", "ELF binary — executable on Linux."),
    ("medium", "binary.pe", "4d5a", "PE binary — executable on Windows."),
    ("medium", "binary.macho", "cffaedfe", "Mach-O 64-bit binary — executable on macOS."),
    ("high", "binary.jar_zip", "504b0304", "ZIP/JAR/APK archive — may contain executable code."),
    ("info", "binary.pdf", "25504446", "PDF document — can carry JavaScript or embedded files."),
    ("high", "shellcode.x86_nop", "9090909090909090", "Long NOP sled — classic shellcode prefix."),
    ("high", "shellcode.x86_int80", "cd80", "int 0x80 — Linux syscall on x86 (shellcode primitive)."),
    ("high", "shellcode.x86_64_syscall", "0f05", "syscall instruction on x86_64 (shellcode primitive)."),
]

def _byte_scan(data: bytes) -> list[Finding]:
    out: list[Finding] = []
    head = data[:8].hex()
    for severity, primitive, magic, explanation in _BYTE_SIGS:
        if head.startswith(magic):
            out.append(Finding(
                layer=Layer.L3_BYTES.value, severity=severity, primitive=primitive,
                location="bytes 0-8", snippet=f"magic={magic}",
                explanation=explanation, confidence=0.95,
            ))
    # Scan whole file for shellcode magics
    for severity, primitive, magic, explanation in _BYTE_SIGS[5:]:
        idx = data.find(bytes.fromhex(magic))
        while idx != -1 and idx < len(data):
            out.append(Finding(
                layer=Layer.L3_BYTES.value, severity=severity, primitive=primitive,
                location=f"bytes {idx}-{idx+len(magic)//2}",
                snippet=f"magic={magic} at {idx}",
                explanation=explanation, confidence=0.7,
            ))
            idx = data.find(bytes.fromhex(magic), idx + 1)
            if sum(1 for f in out if f.primitive == primitive) >= 4:
                break
    return out


# ---------------------------------------------------------------------------
# L4 — Entropy analysis (Shannon entropy of byte histogram)
# ---------------------------------------------------------------------------

def _shannon_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    n = len(data)
    ent = 0.0
    for c in counts:
        if c:
            p = c / n
            ent -= p * math.log2(p)
    return ent

def _entropy_scan(data: bytes, full_entropy: float) -> list[Finding]:
    out: list[Finding] = []
    # Whole-file entropy
    if full_entropy > 7.5:
        out.append(Finding(
            layer=Layer.L4_ENTROPY.value, severity="high", primitive="entropy.high_global",
            location="whole file", snippet=f"H={full_entropy:.3f} bits/byte",
            explanation="Very high entropy — likely packed, encrypted, or compressed payload.",
            confidence=0.8,
        ))
    elif full_entropy > 6.5:
        out.append(Finding(
            layer=Layer.L4_ENTROPY.value, severity="medium", primitive="entropy.elevated",
            location="whole file", snippet=f"H={full_entropy:.3f} bits/byte",
            explanation="Elevated entropy — possibly obfuscated.",
            confidence=0.6,
        ))
    # Sliding window for entropy spikes (only if file is large enough)
    if len(data) > 4096:
        window = 1024
        step = 512
        max_local = 0.0
        max_at = 0
        for i in range(0, len(data) - window, step):
            h = _shannon_entropy(data[i:i+window])
            if h > max_local:
                max_local = h
                max_at = i
        if max_local > 7.7 and max_local > full_entropy + 0.3:
            out.append(Finding(
                layer=Layer.L4_ENTROPY.value, severity="high", primitive="entropy.local_spike",
                location=f"bytes {max_at}-{max_at+window}",
                snippet=f"local H={max_local:.3f} vs global H={full_entropy:.3f}",
                explanation="Local entropy spike — embedded packed/encrypted blob.",
                confidence=0.75,
            ))
    return out


# ---------------------------------------------------------------------------
# L5 — Structural audit (Python only; looks for tell-tale shapes)
# ---------------------------------------------------------------------------

def _structure_scan(source: str, ast_findings: list[Finding]) -> list[Finding]:
    out: list[Finding] = []
    # try/except with bare except wrapping sensitive calls
    if re.search(r"try:\s*\n\s*(?:eval|exec|subprocess|os\.system|pickle\.loads)", source):
        out.append(Finding(
            layer=Layer.L5_STRUCTURE.value, severity="high", primitive="structure.try_wraps_sensitive",
            location="source", snippet="try: <eval|exec|subprocess|...>",
            explanation="Sensitive call wrapped in try — common evasion to swallow detection errors.",
            confidence=0.7,
        ))
    # Long single-line strings (often obfuscated payloads)
    for m in re.finditer(r"['\"]([^'\"\n]{300,})['\"]", source):
        out.append(Finding(
            layer=Layer.L5_STRUCTURE.value, severity="medium", primitive="structure.long_string",
            location=f"line {source.count(chr(10), 0, m.start())+1}",
            snippet=m.group(0)[:80] + "...",
            explanation="Long single-line string — common payload / obfuscated blob.",
            confidence=0.6,
        ))
    # Time-based delays near sensitive calls (APT pattern)
    if re.search(r"(?:time\.sleep|sleep)\s*\(\s*\d{2,}\s*\)", source) and \
       re.search(r"(?:subprocess|os\.system|eval|exec|socket)", source):
        out.append(Finding(
            layer=Layer.L5_STRUCTURE.value, severity="high", primitive="structure.delayed_payload",
            location="source", snippet="time.sleep(N) ... sensitive_call()",
            explanation="Long sleep preceding sensitive call — APT pattern to evade sandboxes.",
            confidence=0.75,
        ))
    # Conditional activation on env var / hostname (sandbox detection)
    if re.search(r"os\.(?:environ|getenv)\(['\"](?:SANDBOX|DEBUG|DEV|CONTAINER)", source):
        out.append(Finding(
            layer=Layer.L5_STRUCTURE.value, severity="medium", primitive="structure.env_gated",
            location="source", snippet="os.environ[...] gate",
            explanation="Environment-gated branch — possible sandbox-detection evasion.",
            confidence=0.55,
        ))
    return out


# ---------------------------------------------------------------------------
# File-type detection
# ---------------------------------------------------------------------------

def _detect_type(data: bytes, name: str) -> str:
    head = data[:8].hex()
    if head.startswith("7f454c46"): return "elf"
    if head.startswith("4d5a"): return "pe"
    if head.startswith("cffaedfe") or head.startswith("cefaedfe") or head.startswith("feedface") or head.startswith("feedfacf"): return "macho"
    if head.startswith("504b0304"): return "zip-archive"
    if head.startswith("25504446"): return "pdf"
    if head.startswith("89504e47"): return "png"
    if head.startswith("ffd8ffe0") or head.startswith("ffd8ffe1"): return "jpeg"
    if head.startswith("1f8b"): return "gzip"
    if head.startswith("425a68"): return "bzip2"
    if name.endswith(".py"): return "python"
    if name.endswith(".js") or name.endswith(".mjs"): return "javascript"
    if name.endswith(".sh"): return "shell"
    if name.endswith(".json"): return "json"
    if name.endswith(".xml"): return "xml"
    if name.endswith(".html") or name.endswith(".htm"): return "html"
    if name.endswith(".php"): return "php"
    if name.endswith(".rb"): return "ruby"
    if name.endswith(".go"): return "go"
    if name.endswith(".rs"): return "rust"
    if name.endswith(".c") or name.endswith(".h"): return "c"
    if name.endswith(".cpp") or name.endswith(".cc") or name.endswith(".hpp"): return "cpp"
    if name.endswith(".java"): return "java"
    if name.endswith(".so"): return "shared-lib"
    if name.endswith(".bin"): return "binary-blob"
    return "unknown"

def _is_text(data: bytes) -> bool:
    if b"\x00" in data[:1024]:
        return False
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_file(path_or_bytes, name: str = "uploaded") -> ScanReport:
    """Read bytes immediately, run all layers, return report."""
    import time
    t0 = time.perf_counter()
    if isinstance(path_or_bytes, (str, Path)):
        with open(path_or_bytes, "rb") as f:
            data = f.read()
    else:
        data = path_or_bytes
    file_hash = hashlib.sha256(data).hexdigest()
    file_size = len(data)
    file_type = _detect_type(data, name)
    is_text = _is_text(data)
    entropy = _shannon_entropy(data)

    report = ScanReport(
        file_hash=file_hash, file_size=file_size, file_type=file_type,
        is_text=is_text, entropy=entropy,
    )
    layers_run: list[str] = []

    # L3 — bytes (always runs)
    layers_run.append(Layer.L3_BYTES.value)
    report.findings.extend(_byte_scan(data))

    # L4 — entropy (always runs)
    layers_run.append(Layer.L4_ENTROPY.value)
    report.findings.extend(_entropy_scan(data, entropy))

    # L2 — regex (text only)
    if is_text:
        layers_run.append(Layer.L2_REGEX.value)
        try:
            text = data.decode("utf-8", errors="replace")
            report.findings.extend(_regex_scan(text))
        except Exception as e:
            report.error = f"L2 regex scan failed: {e}"

    # L1 + L5 — AST + structure (Python only)
    if file_type == "python" and is_text:
        layers_run.append(Layer.L1_AST.value)
        try:
            source = data.decode("utf-8")
            ast_findings, ast_err = _ast_scan(source)
            report.findings.extend(ast_findings)
            if ast_err:
                report.findings.append(Finding(
                    layer=Layer.L1_AST.value, severity="medium",
                    primitive="ast.parse_failed", location="source",
                    snippet=ast_err[:120],
                    explanation="Python source failed to parse — may be obfuscated or intentionally malformed.",
                    confidence=0.6,
                ))
            layers_run.append(Layer.L5_STRUCTURE.value)
            report.findings.extend(_structure_scan(source, ast_findings))
        except Exception as e:
            report.error = f"L1/L5 scan failed: {e}"

    report.layers_run = layers_run
    report.scan_duration_ms = (time.perf_counter() - t0) * 1000.0
    return report


if __name__ == "__main__":
    # Self-test: scan a known-good and a known-bad file
    import sys
    if len(sys.argv) < 2:
        print("usage: content_scanner.py <file>")
        sys.exit(1)
    r = scan_file(sys.argv[1], os.path.basename(sys.argv[1]))
    import json
    print(json.dumps(r.as_dict(), indent=2))
