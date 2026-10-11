#!/bin/bash
# asciiid crash logger — wraps asciiid, captures stderr/stdout, logs crashes
# Usage: scripts/pipeline/asciiid_crashlog.sh [asciiid args...]
#
# Logs to /tmp/asciiid_crash_<timestamp>.log on any non-zero exit.
# Also monitors CPU and captures stack sample if pegged >95% for 30s.

set -u

ASCIIID="$(cd "$(dirname "$0")/../.." && pwd)/.run/asciiid"
LOG_DIR="/tmp"
TS=$(date +%Y%m%d_%H%M%S)
STDOUT_LOG="$LOG_DIR/asciiid_session_${TS}.log"

echo "[crashlog] Starting asciiid at $(date)" | tee "$STDOUT_LOG"
echo "[crashlog] Args: $*" | tee -a "$STDOUT_LOG"
echo "[crashlog] Binary: $ASCIIID" | tee -a "$STDOUT_LOG"
echo "[crashlog] Session log: $STDOUT_LOG" >> "$STDOUT_LOG"
echo "---" >> "$STDOUT_LOG"

# Launch asciiid, capture all output
"$ASCIIID" "$@" >> "$STDOUT_LOG" 2>&1 &
PID=$!
echo "[crashlog] PID: $PID"

# Background monitor for freezes
(
    HIGH=0
    while kill -0 $PID 2>/dev/null; do
        CPU=$(ps -p $PID -o %cpu= 2>/dev/null | tr -d ' ' || echo "0")
        CPU_INT=${CPU%.*}
        if [ "${CPU_INT:-0}" -gt 95 ]; then
            HIGH=$((HIGH + 1))
            if [ "$HIGH" -eq 6 ]; then
                echo "[crashlog] FREEZE: CPU=${CPU}% for 30s+ at $(date)" >> "$STDOUT_LOG"
                sample $PID 1 >> "$STDOUT_LOG" 2>/dev/null || true
            fi
        else
            if [ "$HIGH" -ge 6 ]; then
                echo "[crashlog] RECOVERED from freeze at $(date)" >> "$STDOUT_LOG"
            fi
            HIGH=0
        fi
        sleep 5
    done
) &
MONITOR_PID=$!

# Wait for asciiid to exit
wait $PID 2>/dev/null
EXIT_CODE=$?
kill $MONITOR_PID 2>/dev/null

echo "---" >> "$STDOUT_LOG"
echo "[crashlog] Exited with code $EXIT_CODE at $(date)" >> "$STDOUT_LOG"

if [ $EXIT_CODE -ne 0 ]; then
    CRASH_LOG="$LOG_DIR/asciiid_crash_${TS}.log"
    cp "$STDOUT_LOG" "$CRASH_LOG"

    # Capture macOS crash report if one was generated
    sleep 2
    LATEST_CRASH=$(ls -t ~/Library/Logs/DiagnosticReports/asciiid* 2>/dev/null | head -1)
    if [ -n "$LATEST_CRASH" ]; then
        echo "=== macOS CRASH REPORT ===" >> "$CRASH_LOG"
        head -100 "$LATEST_CRASH" >> "$CRASH_LOG"
    fi

    echo ""
    echo "[crashlog] CRASH detected (exit code $EXIT_CODE)"
    echo "[crashlog] Log: $CRASH_LOG"
    echo "[crashlog] Last 20 lines:"
    tail -20 "$CRASH_LOG" | grep -v '^\[Material\]'
else
    echo "[crashlog] Clean exit"
    rm -f "$STDOUT_LOG" 2>/dev/null
fi
