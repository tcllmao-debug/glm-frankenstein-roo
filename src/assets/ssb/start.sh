#!/usr/bin/env bash
# SSB Z-MARK COMPLETE v16.1 — UNKILLABLE boot (publish fix, hardened).
# The portal is started FIRST and the container's foreground process is an
# infinite supervisor-loop: even if the supervisor/beast crashes or exits,
# the container STAYS ALIVE and the page keeps answering.
set -u

export BEAST_DIR=/app/beast \
       DEFENSE_SCRIPTS=/app/system-wide-defense/scripts \
       PORTAL_DIR=/app/portal \
       SSB_SUPERVISOR_HOME=/tmp/ssb \
       SSB_BEAST_HOME=/app/beast \
       SSB_MONOLITH_FILE_PIN=/app/patched/ssb_monolith_patched.py \
       BEAST_URL=http://127.0.0.1:8787 \
       DEFENSE_URL=http://127.0.0.1:8792 \
       PORTAL_HOST=0.0.0.0 \
       PORTAL_PORT=${PORT:-8080}

mkdir -p /tmp/ssb /app/portal/data

# 1) PORTAL FIRST — instant hub/health on the public port, bound to 0.0.0.0.
echo "[start] portal-first: hub live on :${PORTAL_PORT}"
cd /app/portal && nohup python3 portal_server.py >> /tmp/ssb/portal_boot.log 2>&1 &
cd /app

# 1b) BOOT SELF-CHECK — prove the portal answers before the supervisor spins up.
python3 - <<'PY' >> /tmp/ssb/portal_boot.log 2>&1
import time, urllib.request, os
port = os.environ.get("PORTAL_PORT", "8080")
for i in range(30):
    try:
        code = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2).status
        print(f"[start] portal self-check OK (http {code}) after {i+1} tries")
        break
    except Exception:
        time.sleep(1)
else:
    print("[start] WARNING: portal did not answer within 30s - supervisor loop continues anyway")
PY

# 2) SUPERVISOR LOOP — beast brain, defense layer, hermes autonomy, K3
#    keepalive. Any exit is logged and retried; the loop NEVER exits, so the
#    container (and therefore the published page) can never silently die.
echo "[start] supervisor loop (watchdog + K3 keepalive + hermes mode)"
while true; do
  python3 /app/beast/ssb_supervisor.py >> /tmp/ssb/supervisor.log 2>&1
  rc=$?
  echo "[start] supervisor exited rc=${rc} — restarting in 5s ($(date -u +%H:%M:%S)Z)" >> /tmp/ssb/supervisor.log
  sleep 5
done
