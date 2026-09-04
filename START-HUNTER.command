#!/bin/bash
# Double-click me (Mac): updates the repo, starts the local site, opens the hunter.
cd "$(dirname "$0")"
git pull --quiet 2>/dev/null
# evict whatever holds port 8000 (old static servers included) so the real
# hunter server can bind it
lsof -ti :8000 2>/dev/null | xargs kill -9 2>/dev/null
sleep 1
if launchctl print "gui/$(id -u)/com.field.hunter" >/dev/null 2>&1; then
  # installed as a background service — restart it so it loads the fresh code
  launchctl kickstart -k "gui/$(id -u)/com.field.hunter"
  echo "background service restarted with the latest code"
else
  pkill -f "hunter/serve.py" 2>/dev/null; pkill -f "http.server 8000" 2>/dev/null
  python3 hunter/serve.py 8000 >/tmp/hunter-serve.log 2>&1 &
fi
sleep 1
open "http://localhost:8000/hunter/"
echo "Hunter control panel: http://localhost:8000/hunter/"
echo "log: /tmp/hunter-serve.log"
