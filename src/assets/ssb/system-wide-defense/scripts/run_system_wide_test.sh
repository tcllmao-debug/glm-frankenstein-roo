#!/usr/bin/env bash
# run_system_wide_test.sh — Start scanner with all 4 layers, run tests, capture, exit.
set -e

LOG=/tmp/scanner_system_wide.log
RESULTS=/home/z/my-project/download/system_wide_test_results.json
PIDFILE=/tmp/scanner.pid

# Kill any previous scanner
if [ -f "$PIDFILE" ]; then
    OLD=$(cat "$PIDFILE")
    kill "$OLD" 2>/dev/null || true
    sleep 1
fi
pkill -f scanner_server.py 2>/dev/null || true
sleep 1

# Clear previous state (preserve audit logs)
rm -rf /home/z/my-project/quarantine/* /home/z/my-project/uploads/* /home/z/my-project/baseline
mkdir -p /home/z/my-project/audit_logs

# Start scanner in background of THIS shell
cd /home/z/my-project/scripts
python3 scanner_server.py > "$LOG" 2>&1 &
SCANNER_PID=$!
echo "$SCANNER_PID" > "$PIDFILE"
echo "Scanner PID: $SCANNER_PID"

# Wait for health
echo "Waiting for scanner to come up..."
HEALTH=""
for i in $(seq 1 30); do
    HEALTH=$(curl -s -m 2 http://127.0.0.1:8787/api/health 2>/dev/null || echo "")
    if [ -n "$HEALTH" ]; then
        echo "Scanner healthy after ${i}s"
        echo "$HEALTH"
        break
    fi
    sleep 1
done

if [ -z "$HEALTH" ]; then
    echo "ERROR: scanner did not come up. Log:"
    cat "$LOG"
    exit 1
fi

# Give the system-wide components time to initialize
echo "Waiting 5s for system-wide components to initialize..."
sleep 5

# Verify all 4 layers are active
echo ""
echo "=== SYSTEM DEFENSE STATUS ==="
curl -s http://127.0.0.1:8787/api/system-defense | python3 -m json.tool

echo ""
echo "================================================="
echo "RUNNING SYSTEM-WIDE TESTS"
echo "================================================="
python3 /home/z/my-project/scripts/run_system_wide_tests.py
RC=$?

echo ""
echo "Test runner exit code: $RC"
echo "Scanner still alive?"
ps -p "$SCANNER_PID" > /dev/null 2>&1 && echo "YES (PID $SCANNER_PID)" || echo "NO"

echo ""
echo "=== FINAL FILESYSTEM WATCHER STATS ==="
curl -s http://127.0.0.1:8787/api/filesystem/stats | python3 -m json.tool
echo ""
echo "=== FINAL KERNEL SCANNER STATS ==="
curl -s http://127.0.0.1:8787/api/kernel/stats | python3 -m json.tool
echo ""
echo "=== FINAL SELF-HEAL STATS ==="
curl -s http://127.0.0.1:8787/api/self-heal/stats | python3 -m json.tool

echo ""
echo "=== SCANNER LOG (last 30 lines) ==="
tail -30 "$LOG"

exit $RC
