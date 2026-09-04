#!/bin/bash
# Double-click me (Mac): updates the repo, starts the local site, opens the hunter.
cd "$(dirname "$0")"
git pull --quiet 2>/dev/null
pkill -f "hunter/serve.py" 2>/dev/null; pkill -f "http.server 8000" 2>/dev/null
python3 hunter/serve.py 8000 >/tmp/hunter-serve.log 2>&1 &
sleep 1
open "http://localhost:8000/hunter/"
echo "Hunter control panel: http://localhost:8000/hunter/"
echo "To stop: pkill -f hunter/serve.py   ·   log: /tmp/hunter-serve.log"
