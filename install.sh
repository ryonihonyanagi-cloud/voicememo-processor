#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

print_step()    { echo -e "${GREEN}[OK]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[!]${NC} $1"; }
print_error()   { echo -e "${RED}[X]${NC} $1"; }

echo "========================================"
echo "  Voice Memo Processor - Setup"
echo "========================================"
echo ""

# 1. Check Python3
if ! command -v /opt/homebrew/bin/python3 &>/dev/null; then
    print_error "Python3 not found at /opt/homebrew/bin/python3"
    echo "  Install with: brew install python"
    exit 1
fi
print_step "Python3 found: $(/opt/homebrew/bin/python3 --version)"

# 2. Check ffmpeg
if ! command -v /opt/homebrew/bin/ffmpeg &>/dev/null; then
    print_error "ffmpeg not found at /opt/homebrew/bin/ffmpeg"
    echo "  Install with: brew install ffmpeg"
    exit 1
fi
print_step "ffmpeg found"

# 3. Create virtual environment
if [ ! -d "$SCRIPT_DIR/venv" ]; then
    /opt/homebrew/bin/python3 -m venv "$SCRIPT_DIR/venv"
    print_step "Virtual environment created"
else
    print_step "Virtual environment already exists"
fi

# 4. Install dependencies
source "$SCRIPT_DIR/venv/bin/activate"
pip install -q -r "$SCRIPT_DIR/requirements.txt"
deactivate
print_step "Python dependencies installed"

# 5. Setup configuration interactively
/opt/homebrew/bin/python3 "$SCRIPT_DIR/setup.py"

# 7. Create logs directory
mkdir -p "$SCRIPT_DIR/logs"
print_step "Logs directory ready"

# 8. Make scripts executable
chmod +x "$SCRIPT_DIR/run_voicememo.sh"
chmod +x "$SCRIPT_DIR/process_voicememo.py"
chmod +x "$SCRIPT_DIR/menubar_monitor.py"
print_step "Scripts made executable"

# 9. Load .env to get the USB volume path or use default
if [ -f "$SCRIPT_DIR/.env" ]; then
    source "$SCRIPT_DIR/.env"
fi
VOICEMEMO_MOUNT=${VOICEMEMO_MOUNT:-"/Volumes/VOICEMEMO/RECORD"}
WATCH_PATH=$(dirname "$VOICEMEMO_MOUNT")

# 10. Generate LaunchAgent plist file
PLIST_SRC="$SCRIPT_DIR/com.voicememo.processor.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/com.voicememo.processor.plist"

cat > "$PLIST_SRC" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.voicememo.processor</string>
    <key>ProgramArguments</key>
    <array>
        <string>$SCRIPT_DIR/venv/bin/python3</string>
        <string>$SCRIPT_DIR/process_voicememo.py</string>
    </array>
    <key>WatchPaths</key>
    <array>
        <string>$WATCH_PATH</string>
    </array>
    <key>StandardOutPath</key>
    <string>$SCRIPT_DIR/logs/launchd-out.log</string>
    <key>StandardErrorPath</key>
    <string>$SCRIPT_DIR/logs/launchd-err.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>HOME</key>
        <string>$HOME</string>
    </dict>
    <key>ProcessType</key>
    <string>Background</string>
    <key>LowPriorityIO</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>10</integer>
</dict>
</plist>
print_step "LaunchAgent generated specifically for this system"

if launchctl list 2>/dev/null | grep -q "com.voicememo.processor"; then
    launchctl unload "$PLIST_DEST" 2>/dev/null || true
    print_step "Unloaded existing LaunchAgent"
fi

# 11. Install LaunchAgent (symlink to source)
if [ -L "$PLIST_DEST" ] || [ -f "$PLIST_DEST" ]; then
    rm "$PLIST_DEST"
fi
ln -s "$PLIST_SRC" "$PLIST_DEST"
print_step "LaunchAgent plist linked"

# 11. Load LaunchAgent
launchctl load "$PLIST_DEST"
print_step "LaunchAgent loaded"

# 12. Setup menu bar monitor LaunchAgent
MONITOR_PLIST_SRC="$SCRIPT_DIR/com.voicememo.monitor.plist"
MONITOR_PLIST_DEST="$HOME/Library/LaunchAgents/com.voicememo.monitor.plist"

# Create the plist for the menu bar monitor
cat > "$MONITOR_PLIST_SRC" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.voicememo.monitor</string>
    <key>ProgramArguments</key>
    <array>
        <string>$SCRIPT_DIR/venv/bin/python3</string>
        <string>$SCRIPT_DIR/menubar_monitor.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ProcessType</key>
    <string>Interactive</string>
    <key>StandardOutPath</key>
    <string>$SCRIPT_DIR/logs/monitor-out.log</string>
    <key>StandardErrorPath</key>
    <string>$SCRIPT_DIR/logs/monitor-err.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
PLIST
print_step "Menu bar monitor plist created"

# 13. Unload existing monitor LaunchAgent if present
if launchctl list 2>/dev/null | grep -q "com.voicememo.monitor"; then
    launchctl unload "$MONITOR_PLIST_DEST" 2>/dev/null || true
    print_step "Unloaded existing monitor LaunchAgent"
fi

# 14. Install and load monitor LaunchAgent
if [ -L "$MONITOR_PLIST_DEST" ] || [ -f "$MONITOR_PLIST_DEST" ]; then
    rm "$MONITOR_PLIST_DEST"
fi
ln -s "$MONITOR_PLIST_SRC" "$MONITOR_PLIST_DEST"
launchctl load "$MONITOR_PLIST_DEST"
print_step "Menu bar monitor LaunchAgent loaded"

echo ""
echo "========================================"
echo -e "  ${GREEN}Setup Complete!${NC}"
echo "========================================"
echo ""
echo "The processor will automatically run when VOICEMEMO is mounted."
echo "The menu bar monitor (🎙) is now running."
echo ""
echo "Manual run:  $SCRIPT_DIR/run_voicememo.sh"
echo "View logs:   tail -f $SCRIPT_DIR/logs/voicememo-*.log"
echo ""
echo "LaunchAgent management:"
echo "  Status:     launchctl list | grep voicememo"
echo "  Stop proc:  launchctl unload $PLIST_DEST"
echo "  Start proc: launchctl load $PLIST_DEST"
echo "  Stop menu:  launchctl unload $MONITOR_PLIST_DEST"
echo "  Start menu: launchctl load $MONITOR_PLIST_DEST"
