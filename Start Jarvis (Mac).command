#!/bin/bash
# ============================================================
# Start Jarvis (Mac).command -- double-click to start Jarvis on a Mac.
#
# The FIRST time, macOS may say it "can't be opened because it is from an
# unidentified developer" (it came from the internet). Right-click this
# file -> Open -> Open. After that, double-clicking works.
#
# This window IS Jarvis while it runs: keep it open. Close it, or press
# Ctrl+C in it, to stop Jarvis. Your notes are in the "notes" folder next
# to this file.
# ============================================================
cd "$(dirname "$0")" || exit 1
PORT=4700
URL="http://localhost:$PORT"
mkdir -p logs

echo "================================================"
echo "  Starting Jarvis"
echo "================================================"

# ---- find Python 3.9 or newer ----------------------------------------
PY=""
CANDIDATES="python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 /Library/Frameworks/Python.framework/Versions/Current/bin/python3"
# /usr/bin/python3 is only real once Apple's developer tools are installed;
# otherwise running it pops up an installer, so it's only tried if they are.
if xcode-select -p >/dev/null 2>&1; then CANDIDATES="$CANDIDATES /usr/bin/python3"; fi
for c in $CANDIDATES; do
  p="$(command -v "$c" 2>/dev/null)"
  [ -z "$p" ] && continue
  [ "$p" = "/usr/bin/python3" ] && ! xcode-select -p >/dev/null 2>&1 && continue
  if "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then PY="$p"; break; fi
done

if [ -z "$PY" ]; then
  echo
  echo "Jarvis needs Python 3 (free), and it isn't installed yet."
  read -r -p "Install it now? [y/n]: " ANS
  if [ "$ANS" = "y" ] || [ "$ANS" = "Y" ]; then
    if command -v brew >/dev/null 2>&1; then
      brew install python && PY="$(command -v python3)"
    else
      echo
      echo "Opening python.org. Download the macOS installer, run it,"
      echo "then double-click this file again."
      open "https://www.python.org/downloads/macos/"
      read -r -p "Press Enter to close this window." _
      exit 1
    fi
  else
    echo "OK. Install Python from https://www.python.org/downloads/macos/ when you're ready."
    read -r -p "Press Enter to close this window." _
    exit 1
  fi
fi
echo "Using Python: $PY"

# ---- already running? just open it ------------------------------------
if curl -s -o /dev/null "$URL/model"; then
  echo "Jarvis is already running -- opening it."
  open "$URL"
  exit 0
fi

# ---- start, wait until it answers, open the browser ---------------------
"$PY" -u server.py >> logs/server.log 2>&1 &
SERVER=$!
trap 'echo; echo "Stopping Jarvis..."; kill $SERVER 2>/dev/null; exit 0' INT TERM HUP EXIT
for _ in $(seq 1 40); do
  if curl -s -o /dev/null "$URL/model"; then break; fi
  if ! kill -0 $SERVER 2>/dev/null; then
    echo
    echo "Jarvis didn't start. The last lines of logs/server.log:"
    tail -n 20 logs/server.log
    read -r -p "Press Enter to close this window." _
    exit 1
  fi
  sleep 0.5
done
open "$URL"
echo
echo "Jarvis is running at $URL"
echo "Keep this window open. Close it (or press Ctrl+C) to stop Jarvis."
wait $SERVER
