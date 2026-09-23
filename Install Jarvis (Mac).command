#!/bin/bash
# ============================================================
# Install Jarvis (Mac).command -- run this ONCE after unzipping.
#
# The FIRST time, macOS may say it "can't be opened because it is from an
# unidentified developer" (it came from the internet). Right-click this
# file -> Open -> Open.
#
# It finds Python (and offers to install it if it's missing), installs
# Jarvis in ~/Library/Application Support/Jarvis, and puts Jarvis.app in
# your Applications folder (so it's in Launchpad and Spotlight). After that,
# open Jarvis like any other app. Running this again from a newer download
# updates Jarvis; your notes and settings are kept.
# ============================================================
cd "$(dirname "$0")" || exit 1

echo "================================================"
echo "  Installing Jarvis"
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
echo
if ! "$PY" installer.py; then
  echo
  echo "Something went wrong -- the message above says what."
  read -r -p "Press Enter to close this window." _
  exit 1
fi
echo
echo "Done. Jarvis is starting. From now on, open it from Applications,"
echo "Launchpad or Spotlight (type Jarvis). You can close this window."
sleep 6
