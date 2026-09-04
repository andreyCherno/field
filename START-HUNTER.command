#!/bin/bash
# Double-click me (Mac): updates the repo, starts the local site, opens the hunter.
cd "$(dirname "$0")"
git pull --quiet 2>/dev/null
pkill -f "http.server 8000" 2>/dev/null
python3 -m http.server 8000 >/dev/null 2>&1 &
sleep 1
open "http://localhost:8000/hunter/"
echo "Hunter is running at http://localhost:8000/hunter/"
echo "Close this window to keep it running; run STOP by: pkill -f http.server"
