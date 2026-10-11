#!/usr/bin/env bash
# Start the whole system: the shelf-check model (port 8790) and the staff app's bridge (port 8787).
# The phone app then runs from staff-app/mobile with `npx expo start --lan` (see staff-app/README.md).
set -euo pipefail
cd "$(dirname "$0")"
PY=".venv/bin/python"; [ -x "$PY" ] || PY="python3"

"$PY" app/server.py --port 8790 &
MODEL=$!
trap 'kill $MODEL 2>/dev/null' EXIT
for _ in $(seq 60); do curl -sf -o /dev/null http://127.0.0.1:8790/samples && break; sleep 1; done   # wait for the model

echo "Shelf-check model:  http://127.0.0.1:8790/gates   (test run + score)"
echo "Staff app bridge:   http://127.0.0.1:8787         (drop camera pictures)"
echo "Manager dashboard:  http://127.0.0.1:8787/manager.html"
node staff-app/bridge/server.js
