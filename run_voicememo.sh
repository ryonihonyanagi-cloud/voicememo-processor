#!/bin/bash
# run_voicememo.sh -- Shell wrapper for launchd
# Triggered when /Volumes/VOICEMEMO appears via WatchPaths

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="$SCRIPT_DIR/logs/wrapper.log"
LOCK_FILE="/tmp/voicememo-processor.lock"

mkdir -p "$SCRIPT_DIR/logs"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

# Prevent concurrent runs
if [ -f "$LOCK_FILE" ]; then
    pid=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        log "Another instance is running (PID $pid), exiting"
        exit 0
    fi
    rm -f "$LOCK_FILE"
fi

echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

# Load env configuring mount point
if [ -f "$SCRIPT_DIR/.env" ]; then
    source "$SCRIPT_DIR/.env"
fi
VOICEMEMO_MOUNT=${VOICEMEMO_MOUNT:-"/Volumes/VOICEMEMO/RECORD"}

# Wait for volume to stabilize after USB mount
sleep 3

# Check if VOICEMEMO is actually mounted
if [ ! -d "$VOICEMEMO_MOUNT" ]; then
    log "VOICEMEMO ($VOICEMEMO_MOUNT) not mounted, exiting manual check (but Python handles skipping Phase 1)"
fi

log "VOICEMEMO detected, starting processing"

# Activate virtual environment and run
cd "$SCRIPT_DIR"
source "$SCRIPT_DIR/venv/bin/activate"

python3 "$SCRIPT_DIR/process_voicememo.py" 2>&1 | tee -a "$LOG_FILE"
exit_code=${PIPESTATUS[0]}

log "Processing finished with exit code $exit_code"
