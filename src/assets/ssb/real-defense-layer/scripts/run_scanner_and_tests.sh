#!/usr/bin/env bash
# run_scanner_and_tests.sh — Single-shot: start scanner, wait, run tests, capture, exit.
set -e

LOG=/tmp/scanner.log
RESULTS=/home/z/my-project/download/real_test_results.json
PIDFILE=/tmp/scanner.pid

# Kill any previous scanner
if [ -f "$PIDFILE" ]; then
    OLD=$(cat "$PIDFILE")
    kill "$OLD" 2>/dev/null || true
    sleep 1
fi
pkill -f scanner_server.py 2>/dev/null || true
sleep 1

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

# Run the tests
echo ""
echo "================================================="
echo "RUNNING REAL TESTS"
echo "================================================="
python3 /home/z/my-project/scripts/run_real_tests.py
RC=$?

echo ""
echo "Test runner exit code: $RC"
echo "Scanner still alive?"
ps -p "$SCANNER_PID" > /dev/null 2>&1 && echo "YES (PID $SCANNER_PID)" || echo "NO"

# Leave the scanner running for further inspection
echo "Scanner log tail:"
tail -20 "$LOG"

exit $RC
