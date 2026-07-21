"""
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
