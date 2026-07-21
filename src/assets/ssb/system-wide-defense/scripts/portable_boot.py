#!/usr/bin/env python3
"""
portable_boot.py — ADDITIVE portability shim for the SSB defense layer.

The original defense modules hardcode the author's home (`/home/z/...`).
This shim does NOT modify the original files. It:
  1. copies the scripts directory into a writable runtime dir,
  2. rewrites the hardcoded path prefixes inside the COPIES only,
  3. boots uvicorn against the patched copies.

Env:
  SSB_DEFENSE_HOME     new home prefix      (default: ~/.ssb_defense/home)
  SSB_DEFENSE_RUNTIME  runtime copy dir     (default: ~/.ssb_defense/runtime)
  SSB_DEFENSE_PORT     listen port          (default: 8792)
  SSB_DEFENSE_HOST     listen host          (default: 127.0.0.1)
"""
import os
import pathlib
import shutil
import sys

SRC = pathlib.Path(__file__).resolve().parent
HOME_PREFIX = os.environ.get("SSB_DEFENSE_HOME", str(pathlib.Path.home() / ".ssb_defense" / "home"))
RUNTIME = pathlib.Path(os.environ.get("SSB_DEFENSE_RUNTIME", str(pathlib.Path.home() / ".ssb_defense" / "runtime")))
PORT = int(os.environ.get("SSB_DEFENSE_PORT", "8792"))
HOST = os.environ.get("SSB_DEFENSE_HOST", "127.0.0.1")

BASE = HOME_PREFIX + "/my-project"

def main() -> int:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    (pathlib.Path(BASE)).mkdir(parents=True, exist_ok=True)
    patched = 0
    for src in sorted(SRC.glob("*.py")):
        if src.name == pathlib.Path(__file__).name:
            continue
        dst = RUNTIME / src.name
        data = src.read_bytes()
        # longer prefix first so /home/z/my-project wins over /home/z
        new = data.replace(b"/home/z/my-project", BASE.encode())
        new = new.replace(b"/home/z", HOME_PREFIX.encode())
        if new != data:
            patched += 1
        dst.write_bytes(new)
    print(f"[portable_boot] runtime={RUNTIME} home={HOME_PREFIX} patched_files={patched}", flush=True)
    os.chdir(RUNTIME)
    os.execvp(
        sys.executable,
        [sys.executable, "-m", "uvicorn", "scanner_server:app",
         "--host", HOST, "--port", str(PORT), "--app-dir", str(RUNTIME)],
    )
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
