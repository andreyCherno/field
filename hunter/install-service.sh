#!/bin/bash
# Install the hunter as a permanent macOS background service (launchd).
# After this, serve.py runs always — at login, after crashes — and the UI
# is simply always alive at http://localhost:8000/hunter/.
#
#   ./hunter/install-service.sh          install / update + start
#   ./hunter/install-service.sh remove   stop + uninstall
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.field.hunter.plist"
LABEL="com.field.hunter"

if [ "$1" = "remove" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "hunter service removed"
  exit 0
fi

PY="$(command -v python3)"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$PY</string><string>$REPO/hunter/serve.py</string><string>8000</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/hunter-serve.log</string>
  <key>StandardErrorPath</key><string>/tmp/hunter-serve.log</string>
  <key>EnvironmentVariables</key><dict>
    <key>ANTHROPIC_API_KEY</key><string>${ANTHROPIC_API_KEY:-}</string>
  </dict>
</dict></plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
sleep 1
if curl -s -o /dev/null http://localhost:8000/hunter/; then
  echo "✓ hunter is running as a background service: http://localhost:8000/hunter/"
  echo "  it now starts by itself at every login and restarts if it crashes"
  echo "  log: /tmp/hunter-serve.log · remove with: ./hunter/install-service.sh remove"
else
  echo "service installed but not answering yet — check /tmp/hunter-serve.log"
fi
[ -z "$ANTHROPIC_API_KEY" ] && echo "note: ANTHROPIC_API_KEY was not set in this shell — run 'export ANTHROPIC_API_KEY=...' and rerun this script so the service gets it"
