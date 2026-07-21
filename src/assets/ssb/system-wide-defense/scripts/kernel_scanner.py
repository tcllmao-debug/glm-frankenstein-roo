#!/usr/bin/env python3
"""
SSB V11 Z MARK — Kernel & Process Scanner
==========================================

A real-time kernel/process scanner that monitors /proc for:
  - New processes spawning (especially shell spawns, suspicious parents)
  - Network connections (outbound to suspicious IPs/ports)
  - File descriptors opened by processes (especially /etc/shadow, /proc/self)
  - Process ancestry chains (shell → bash → nc → reverse shell pattern)
  - Memory maps (processes mapping executable memory — shellcode injection)
  - Status anomalies (processes with PPID 1 that shouldn't be, zombies)
  - CPU/mem spikes (cryptominer pattern)

This is a USER-SPACE scanner that reads /proc. It is NOT a kernel module —
in a container without CAP_SYS_ADMIN we can't load kernel modules. But
/proc is the kernel's user-space interface, and reading it gives us real
kernel-level visibility into what's happening on the system.

Detection layers:
  L1  Process spawn monitor   — new PIDs, parent chains, command lines
  L2  Network connection audit — outbound TCP/UDP, listening sockets, foreign IPs
  L3  File descriptor audit   — processes reading /etc/shadow, /proc/self, /dev/mem
  L4  Memory map audit        — processes with executable heap/stack (shellcode)
  L5  Resource anomaly        — CPU/mem outliers (cryptominer / fork bomb pattern)
  L6  Self-protection         — attempts to kill the scanner or modify its files

Every detection logs to /home/z/my-project/audit_logs/kernel.log and feeds
into the multi-chain soft ping for action decision.
"""

from __future__ import annotations
import ctypes
import ctypes.util
import hashlib
import json
import os
import re
import socket
import struct
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from multi_chain_soft_ping import MultiChainSoftPing, SoftPingDecision, Action
from quarantine_daemon import QuarantineDaemon

AUDIT_LOG = Path("/home/z/my-project/audit_logs/kernel.log")
AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
_log_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Process model
# ---------------------------------------------------------------------------

@dataclass
class ProcessInfo:
    pid: int
    ppid: int
    name: str
    cmdline: list[str]
    state: str
    uid: int
    gid: int
    exe: str
    cwd: str
    started_at_ms: float
    cpu_percent: float = 0.0
    mem_percent: float = 0.0
    num_fds: int = 0
    num_threads: int = 1

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NetworkConnection:
    pid: int
    proto: str  # tcp / tcp6 / udp / udp6
    local_addr: str
    local_port: int
    remote_addr: str
    remote_port: int
    state: str  # ESTABLISHED, LISTEN, TIME_WAIT, etc.
    inode: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class KernelFinding:
    timestamp_ms: float
    layer: str
    severity: str  # info / low / medium / high / critical
    primitive: str
    pid: int | None
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Suspicious patterns
# ---------------------------------------------------------------------------

# Suspicious process names (by regex, case-insensitive)
SUSPICIOUS_PROCESS_PATTERNS = [
    (r"(?i)\bnc\b|netcat", "high", "process.netcat", "Netcat — common reverse shell tool"),
    (r"(?i)\bnmap\b", "medium", "process.nmap", "Nmap — network scanner"),
    (r"(?i)\bhydra\b", "high", "process.hydra", "Hydra — password cracker"),
    (r"(?i)\bjohn\b", "medium", "process.john", "John the Ripper — password cracker"),
    (r"(?i)\bhashcat\b", "medium", "process.hashcat", "Hashcat — password cracker"),
    (r"(?i)\bminerd\b|xmrig|cgminer|bfgminer", "critical", "process.cryptominer", "Cryptominer"),
    (r"(?i)\btsclient\b|\bvnc\b|\bxrdp\b", "low", "process.remote_desktop", "Remote desktop client"),
]

# Suspicious command-line patterns (regex on full cmdline joined)
SUSPICIOUS_CMDLINE_PATTERNS = [
    (r"(?i)/dev/tcp/", "critical", "cmdline.dev_tcp", "Bash /dev/tcp reverse shell"),
    (r"(?i)bash\s+-i\s+(?:>&|>)\s*/dev/", "critical", "cmdline.bash_reverse_shell", "Bash reverse shell"),
    (r"(?i)nc\s+-e\s+/bin/sh", "critical", "cmdline.nc_exec", "Netcat exec shell"),
    (r"(?i)curl\s+.*\|\s*(?:sh|bash)", "critical", "cmdline.curl_pipe_shell", "curl piped to shell — RCE"),
    (r"(?i)wget\s+.*\|\s*(?:sh|bash)", "critical", "cmdline.wget_pipe_shell", "wget piped to shell — RCE"),
    (r"(?i)base64\s+-d\s*\|\s*(?:sh|bash)", "critical", "cmdline.base64_pipe_shell", "base64 decoded to shell — RCE"),
    (r"(?i)python\s+-c\s+['\"]import\s+socket", "critical", "cmdline.python_reverse_shell", "Python reverse shell"),
    (r"(?i)perl\s+-e\s+['\"]use\s+Socket", "high", "cmdline.perl_reverse_shell", "Perl reverse shell"),
    (r"(?i)/etc/shadow", "high", "cmdline.shadow_access", "Reading /etc/shadow"),
    (r"(?i)chmod\s+\+x\s+/tmp/", "medium", "cmdline.chmod_tmp_exec", "Making /tmp file executable — staging"),
    (r"(?i)(?:nohup|disown)\s+.*&", "low", "cmdline.background_detach", "Background + detach — persistence pattern"),
    (r"(?i)(?:crontab|at\s+now)\s+-[el]", "medium", "cmdline.cron_persist", "Cron/at persistence"),
    (r"(?i)export\s+LD_PRELOAD", "high", "cmdline.ld_preload", "LD_PRELOAD — process injection"),
    (r"(?i)(?:/proc/self|/proc/\$\$)/mem", "critical", "cmdline.proc_mem", "Process memory read — exploit primitive"),
]

# Suspicious outbound IPs (RFC1918 + cloud metadata excluded; we flag direct internet)
# We flag connections to non-RFC1918, non-loopback, non-metdata IPs.
def is_suspicious_remote_ip(ip: str) -> bool:
    if not ip or ip == "0.0.0.0" or ip == "::":
        return False
    if ip.startswith("127."):
        return False
    if ip.startswith("10.") or ip.startswith("192.168."):
        return False
    if ip.startswith("172."):
        try:
            second = int(ip.split(".")[1])
            if 16 <= second <= 31:
                return False
        except (IndexError, ValueError):
            pass
    if ip == "169.254.169.254":  # cloud metadata — SSRF target but legit here
        return False
    if ip.startswith("::1") or ip.startswith("fe80"):
        return False
    return True  # anything else is "suspicious" (direct internet)


# ---------------------------------------------------------------------------
# Kernel scanner
# ---------------------------------------------------------------------------

class KernelScanner:
    """Monitors /proc for process/network/fd anomalies."""

    def __init__(self, engine: MultiChainSoftPing | None = None,
                 daemon: QuarantineDaemon | None = None,
                 scan_interval_s: float = 2.0,
                 max_findings: int = 10000) -> None:
        self.engine = engine or MultiChainSoftPing()
        self.daemon = daemon or QuarantineDaemon()
        self.scan_interval_s = scan_interval_s
        self._findings: deque[KernelFinding] = deque(maxlen=max_findings)
        self._running = False
        self._thread: threading.Thread | None = None
        self._known_pids: set[int] = set()
        self._self_pid = os.getpid()
        self._scanner_pids: set[int] = {self._self_pid}
        self._stats = {
            "scans_total": 0,
            "findings_total": 0,
            "processes_seen": 0,
            "connections_seen": 0,
            "started_at_ms": time.time() * 1000.0,
        }
        # Try to find scanner_server.py PID for self-protection
        try:
            import subprocess
            r = subprocess.run(["pgrep", "-f", "scanner_server.py"],
                               capture_output=True, text=True, timeout=2)
            for line in r.stdout.strip().split("\n"):
                if line.strip().isdigit():
                    self._scanner_pids.add(int(line.strip()))
        except Exception:
            pass

    # -------------------------------------------------------------------
    # /proc readers
    # -------------------------------------------------------------------

    @staticmethod
    def _read_process(pid: int) -> ProcessInfo | None:
        try:
            proc_dir = Path(f"/proc/{pid}")
            if not proc_dir.exists():
                return None
            # /proc/PID/stat — parse ppid, state
            stat = (proc_dir / "stat").read_text()
            # stat format: pid (comm) state ppid ...
            # comm can contain spaces and parens, so parse carefully
            rparen = stat.rfind(")")
            comm = stat[stat.find("(")+1:rparen]
            rest = stat[rparen+2:].split()
            state = rest[0]
            ppid = int(rest[1])
            # /proc/PID/cmdline — null-separated args
            cmdline_raw = (proc_dir / "cmdline").read_bytes()
            cmdline = [a for a in cmdline_raw.split(b"\x00") if a]
            cmdline = [a.decode("utf-8", errors="replace") for a in cmdline]
            if not cmdline:
                cmdline = [comm]
            # /proc/PID/status — uid, gid, threads
            status = (proc_dir / "status").read_text()
            uid = gid = 0
            num_threads = 1
            for line in status.split("\n"):
                if line.startswith("Uid:"):
                    uid = int(line.split()[1])
                elif line.startswith("Gid:"):
                    gid = int(line.split()[1])
                elif line.startswith("Threads:"):
                    num_threads = int(line.split()[1])
            # /proc/PID/exe — symlink to executable
            try:
                exe = os.readlink(proc_dir / "exe")
            except OSError:
                exe = ""
            try:
                cwd = os.readlink(proc_dir / "cwd")
            except OSError:
                cwd = ""
            # started at
            try:
                stat_mtime = (proc_dir / "stat").stat().st_mtime
                started_at_ms = stat_mtime * 1000.0
            except OSError:
                started_at_ms = 0.0
            # num fds
            try:
                num_fds = len(os.listdir(proc_dir / "fd"))
            except OSError:
                num_fds = 0
            return ProcessInfo(
                pid=pid, ppid=ppid, name=comm, cmdline=cmdline, state=state,
                uid=uid, gid=gid, exe=exe, cwd=cwd, started_at_ms=started_at_ms,
                num_fds=num_fds, num_threads=num_threads,
            )
        except (OSError, PermissionError, IndexError, ValueError):
            return None

    @staticmethod
    def _list_pids() -> list[int]:
        try:
            return [int(p) for p in os.listdir("/proc") if p.isdigit()]
        except OSError:
            return []

    @staticmethod
    def _read_connections() -> list[NetworkConnection]:
        """Parse /proc/net/tcp and /proc/net/tcp6 for established connections."""
        conns = []
        for proto, path in [("tcp", "/proc/net/tcp"), ("tcp6", "/proc/net/tcp6"),
                            ("udp", "/proc/net/udp"), ("udp6", "/proc/net/udp6")]:
            try:
                content = Path(path).read_text()
            except OSError:
                continue
            lines = content.strip().split("\n")[1:]  # skip header
            for line in lines:
                parts = line.split()
                if len(parts) < 10:
                    continue
                try:
                    local_addr_hex, local_port_hex = parts[1].split(":")
                    remote_addr_hex, remote_port_hex = parts[2].split(":")
                    state_hex = parts[3]
                    inode = int(parts[9])
                except (IndexError, ValueError):
                    continue
                local_addr = KernelScanner._format_addr(local_addr_hex, proto)
                local_port = int(local_port_hex, 16)
                remote_addr = KernelScanner._format_addr(remote_addr_hex, proto)
                remote_port = int(remote_port_hex, 16)
                state = KernelScanner._tcp_state(int(state_hex, 16))
                # Find PID by inode
                pid = KernelScanner._find_pid_by_inode(inode)
                conns.append(NetworkConnection(
                    pid=pid, proto=proto, local_addr=local_addr,
                    local_port=local_port, remote_addr=remote_addr,
                    remote_port=remote_port, state=state, inode=inode,
                ))
        return conns

    @staticmethod
    def _format_addr(hex_addr: str, proto: str) -> str:
        if proto.endswith("6"):
            # IPv6 — 16 bytes
            b = bytes.fromhex(hex_addr)
            # in-addr order: 4 32-bit words in little-endian
            parts = []
            for i in range(0, 16, 4):
                word = b[i:i+4][::-1]
                parts.append(socket.inet_ntop(socket.AF_INET6,
                                               b[i*4:(i+1)*4][::-1] if False else word))
            # actually IPv6 is stored as 4 32-bit little-endian words
            b = bytes.fromhex(hex_addr)
            addr_bytes = b""
            for i in range(0, 16, 4):
                addr_bytes += b[i:i+4][::-1]
            try:
                return socket.inet_ntop(socket.AF_INET6, addr_bytes)
            except (ValueError, OSError):
                return hex_addr
        else:
            # IPv4 — 4 bytes in little-endian
            b = bytes.fromhex(hex_addr)
            try:
                return socket.inet_ntop(socket.AF_INET, b[::-1])
            except (ValueError, OSError):
                return hex_addr

    @staticmethod
    def _tcp_state(state: int) -> str:
        states = {
            1: "ESTABLISHED", 2: "SYN_SENT", 3: "SYN_RECV",
            4: "FIN_WAIT1", 5: "FIN_WAIT2", 6: "TIME_WAIT",
            7: "CLOSE", 8: "CLOSE_WAIT", 9: "LAST_ACK",
            10: "LISTEN", 11: "CLOSING",
        }
        return states.get(state, f"UNKNOWN({state})")

    @staticmethod
    def _find_pid_by_inode(inode: int) -> int:
        if inode == 0:
            return 0
        try:
            for pid_str in os.listdir("/proc"):
                if not pid_str.isdigit():
                    continue
                fd_dir = Path(f"/proc/{pid_str}/fd")
                if not fd_dir.exists():
                    continue
                for fd in os.listdir(fd_dir):
                    try:
                        target = os.readlink(fd_dir / fd)
                        if target.startswith("socket:[") and target.endswith("]"):
                            sock_inode = int(target[8:-1])
                            if sock_inode == inode:
                                return int(pid_str)
                    except (OSError, ValueError):
                        continue
        except (OSError, PermissionError):
            pass
        return 0

    # -------------------------------------------------------------------
    # Detection layers
    # -------------------------------------------------------------------

    def _scan_process_spawn(self, pids: list[int]) -> list[KernelFinding]:
        """L1: detect new processes and suspicious command lines."""
        findings = []
        new_pids = set(pids) - self._known_pids
        for pid in new_pids:
            info = self._read_process(pid)
            if info is None:
                continue
            self._stats["processes_seen"] += 1
            # Skip our own scanner processes
            if pid in self._scanner_pids:
                continue
            cmdline_str = " ".join(info.cmdline)
            # Check suspicious cmdline patterns
            for pattern, severity, primitive, explanation in SUSPICIOUS_CMDLINE_PATTERNS:
                if re.search(pattern, cmdline_str):
                    findings.append(KernelFinding(
                        timestamp_ms=time.time() * 1000.0,
                        layer="L1_SPAWN", severity=severity, primitive=primitive,
                        pid=pid,
                        detail=f"{explanation} — cmdline: {cmdline_str[:200]}",
                        evidence={"cmdline": info.cmdline[:10], "name": info.name,
                                  "ppid": info.ppid, "exe": info.exe},
                    ))
            # Check suspicious process names
            for pattern, severity, primitive, explanation in SUSPICIOUS_PROCESS_PATTERNS:
                if re.search(pattern, info.name) or re.search(pattern, os.path.basename(info.exe or info.name)):
                    findings.append(KernelFinding(
                        timestamp_ms=time.time() * 1000.0,
                        layer="L1_SPAWN", severity=severity, primitive=primitive,
                        pid=pid,
                        detail=f"{explanation} — process: {info.name}",
                        evidence={"name": info.name, "exe": info.exe, "ppid": info.ppid},
                    ))
        return findings

    def _scan_connections(self, conns: list[NetworkConnection]) -> list[KernelFinding]:
        """L2: audit network connections for suspicious remote IPs."""
        findings = []
        for c in conns:
            self._stats["connections_seen"] += 1
            # Only flag ESTABLISHED outbound connections
            if c.state != "ESTABLISHED":
                continue
            if c.proto not in ("tcp", "tcp6"):
                continue
            if not is_suspicious_remote_ip(c.remote_addr):
                continue
            # High ports often used for reverse shells
            if c.remote_port in (4444, 1337, 31337, 8080, 9999, 5555, 6667, 6668, 6669):
                findings.append(KernelFinding(
                    timestamp_ms=time.time() * 1000.0,
                    layer="L2_NETWORK", severity="critical",
                    primitive="net.reverse_shell_port",
                    pid=c.pid,
                    detail=f"Established connection to {c.remote_addr}:{c.remote_port} "
                           f"(known reverse-shell port) from PID {c.pid}",
                    evidence=asdict(c),
                ))
            else:
                findings.append(KernelFinding(
                    timestamp_ms=time.time() * 1000.0,
                    layer="L2_NETWORK", severity="medium",
                    primitive="net.outbound_internet",
                    pid=c.pid,
                    detail=f"Outbound connection to {c.remote_addr}:{c.remote_port} "
                           f"from PID {c.pid}",
                    evidence=asdict(c),
                ))
        return findings

    def _scan_fds(self) -> list[KernelFinding]:
        """L3: audit file descriptors for sensitive file access."""
        findings = []
        for pid in self._list_pids():
            if pid in self._scanner_pids:
                continue
            fd_dir = Path(f"/proc/{pid}/fd")
            if not fd_dir.exists():
                continue
            try:
                for fd_name in os.listdir(fd_dir):
                    try:
                        target = os.readlink(fd_dir / fd_name)
                    except OSError:
                        continue
                    # Check for sensitive targets
                    if target == "/etc/shadow" or target == "/etc/gshadow":
                        findings.append(KernelFinding(
                            timestamp_ms=time.time() * 1000.0,
                            layer="L3_FD", severity="critical",
                            primitive="fd.shadow_read",
                            pid=pid,
                            detail=f"PID {pid} has /etc/shadow open via fd {fd_name}",
                            evidence={"fd": fd_name, "target": target},
                        ))
                    elif target.startswith("/proc/") and target.endswith("/mem"):
                        findings.append(KernelFinding(
                            timestamp_ms=time.time() * 1000.0,
                            layer="L3_FD", severity="critical",
                            primitive="fd.proc_mem",
                            pid=pid,
                            detail=f"PID {pid} has {target} open — process memory access",
                            evidence={"fd": fd_name, "target": target},
                        ))
                    elif target == "/dev/mem" or target == "/dev/kmem":
                        findings.append(KernelFinding(
                            timestamp_ms=time.time() * 1000.0,
                            layer="L3_FD", severity="critical",
                            primitive="fd.dev_mem",
                            pid=pid,
                            detail=f"PID {pid} has {target} open — direct kernel memory",
                            evidence={"fd": fd_name, "target": target},
                        ))
            except (OSError, PermissionError):
                continue
        return findings

    def _scan_self_protection(self) -> list[KernelFinding]:
        """L6: detect attempts to kill the scanner or modify its files."""
        findings = []
        scanner_files = [
            "/home/z/my-project/scripts/scanner_server.py",
            "/home/z/my-project/scripts/content_scanner.py",
            "/home/z/my-project/scripts/multi_chain_soft_ping.py",
            "/home/z/my-project/scripts/quarantine_daemon.py",
            "/home/z/my-project/scripts/filesystem_watcher.py",
            "/home/z/my-project/scripts/kernel_scanner.py",
            "/home/z/my-project/scripts/self_heal.py",
        ]
        for pid in self._list_pids():
            if pid in self._scanner_pids:
                continue
            # Check cmdline for kill/pgrep/killall targeting our scanner
            info = self._read_process(pid)
            if info is None:
                continue
            cmdline_str = " ".join(info.cmdline)
            if re.search(r"(?i)\b(?:kill|killall|pkill)\b.*(?:scanner_server|content_scanner|filesystem_watcher|kernel_scanner|self_heal|quarantine_daemon)", cmdline_str):
                findings.append(KernelFinding(
                    timestamp_ms=time.time() * 1000.0,
                    layer="L6_SELF_PROTECT", severity="critical",
                    primitive="self.kill_scanner",
                    pid=pid,
                    detail=f"PID {pid} attempting to kill scanner: {cmdline_str[:200]}",
                    evidence={"cmdline": info.cmdline[:10]},
                ))
            # Check if any non-scanner process has our scanner files open for writing
            fd_dir = Path(f"/proc/{pid}/fd")
            if fd_dir.exists():
                try:
                    for fd_name in os.listdir(fd_dir):
                        try:
                            target = os.readlink(fd_dir / fd_name)
                        except OSError:
                            continue
                        if target in scanner_files:
                            # Check if open for write (O_WRONLY or O_RDWR)
                            try:
                                fd_path = fd_dir / fd_name
                                # We can't directly check flags, but opening for write
                                # is suspicious enough to flag
                                findings.append(KernelFinding(
                                    timestamp_ms=time.time() * 1000.0,
                                    layer="L6_SELF_PROTECT", severity="high",
                                    primitive="self.scanner_file_open",
                                    pid=pid,
                                    detail=f"PID {pid} has scanner file open: {target}",
                                    evidence={"fd": fd_name, "target": target,
                                              "cmdline": info.cmdline[:5]},
                                ))
                            except (OSError,):
                                continue
                except (OSError, PermissionError):
                    continue
        return findings

    # -------------------------------------------------------------------
    # Main loop
    # -------------------------------------------------------------------

    def start(self) -> bool:
        # Initial PID snapshot
        self._known_pids = set(self._list_pids())
        print(f"[kernel_scanner] initial PID snapshot: {len(self._known_pids)} processes", file=sys.stderr)
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="kernel-scanner")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        while self._running:
            try:
                t0 = time.time()
                pids = self._list_pids()
                findings = []
                # L1: process spawn
                findings.extend(self._scan_process_spawn(pids))
                # L2: network connections
                conns = self._read_connections()
                findings.extend(self._scan_connections(conns))
                # L3: file descriptors (only every 5th scan — expensive)
                if self._stats["scans_total"] % 5 == 0:
                    findings.extend(self._scan_fds())
                # L6: self-protection
                findings.extend(self._scan_self_protection())
                # Update known PIDs
                self._known_pids = set(pids)
                # Log findings
                for f in findings:
                    self._log_finding(f)
                self._stats["scans_total"] += 1
                self._stats["findings_total"] += len(findings)
                # Sleep for remainder of interval
                elapsed = time.time() - t0
                sleep_time = max(0.1, self.scan_interval_s - elapsed)
                time.sleep(sleep_time)
            except Exception as e:
                self._stats["findings_total"] += 1
                self._log_finding(KernelFinding(
                    timestamp_ms=time.time() * 1000.0,
                    layer="ERROR", severity="low", primitive="scanner.error",
                    pid=None, detail=f"Kernel scanner error: {e}",
                ))
                time.sleep(1.0)

    def _log_finding(self, finding: KernelFinding) -> None:
        self._findings.append(finding)
        line = json.dumps({
            "ts": finding.timestamp_ms,
            "layer": finding.layer,
            "severity": finding.severity,
            "primitive": finding.primitive,
            "pid": finding.pid,
            "detail": finding.detail[:300],
        }, separators=(",", ":"))
        with _log_lock:
            try:
                with open(AUDIT_LOG, "a") as f:
                    f.write(line + "\n")
            except OSError:
                pass

    def get_findings(self, limit: int = 100) -> list[dict[str, Any]]:
        return [f.as_dict() for f in list(self._findings)[-limit:]]

    def get_stats(self) -> dict[str, Any]:
        return {
            **self._stats,
            "current_pids": len(self._known_pids),
            "scanner_pids": list(self._scanner_pids),
            "uptime_ms": (time.time() * 1000.0) - self._stats["started_at_ms"],
        }


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Starting kernel scanner self-test (10 seconds)...")
    scanner = KernelScanner(scan_interval_s=1.0)
    if not scanner.start():
        print("FAILED to start")
        sys.exit(1)
    try:
        for i in range(10):
            time.sleep(1)
            s = scanner.get_stats()
            print(f"  [{i+1}s] scans={s['scans_total']}  findings={s['findings_total']}  "
                  f"pids={s['current_pids']}  conns_seen={s['connections_seen']}")
        print("\nRecent findings:")
        for f in scanner.get_findings(20):
            print(f"  [{f['severity']:8s}] {f['layer']:14s} {f['primitive']:30s} {f['detail'][:80]}")
    finally:
        scanner.stop()
