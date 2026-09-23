JARVIS BROWSER LAUNCHERS
=========================

Tab-level tracking only works when your browser is running with "remote
debugging" turned on. Browsers don't expose this by default (for good
security reasons), so it has to be turned on via a command-line flag —
which means launching via one of these .bat files instead of your normal
desktop icon, on days you want Jarvis to see your active tab.

NORMALLY YOU WON'T RUN THESE DIRECTLY ANYMORE
----------------------------------------------
As of 1.9.3, "Start Jarvis.bat" in the main my-jarvis folder does the
whole boot for you (server + overlay + browser choice) in one go, and
calls one of these four files for you based on which browser you pick.
This README is still here for:
  - understanding what's actually happening under the hood, and
  - running a browser launcher on its own if you ever want to.

HOW TO USE ON THEIR OWN
------------------------
1. Fully close your browser first (all windows) if it's already open.
   A Chromium-based browser only accepts the debug flag on a fresh start —
   if it's already running, double-clicking the .bat won't do anything
   because it just opens a new window in the already-running instance.

2. Double-click the .bat file for whichever browser you use that day:
     - start-opera-gx.bat
     - start-chrome.bat
     - start-edge.bat
     - start-brave.bat
   Each one now also accepts an optional URL as its first argument
   (that's how "Start Jarvis.bat" tells it to open straight to Jarvis) —
   e.g. start-chrome.bat http://localhost:4700 — but double-clicking with
   no argument still works exactly as before and just opens the browser.

3. Use your browser completely normally. Jarvis (via tab_watcher.py) will
   be able to see the title and URL of whichever tab is currently active.

4. When you're done, just close the browser normally. Nothing is left
   running in the background, and nothing changes about your browser
   permanently — the debug flag only applies to that one running session.

WHY A SEPARATE PROFILE FOLDER?
-------------------------------
Each .bat launches with its own --user-data-dir pointing at a folder
under jarvis-profiles\ next to this README. This is deliberate, not a
mistake:
  - It's what lets you close your EVERYDAY browser window (without the
    flag) and the debug-enabled one side by side without them fighting
    over the same profile lock.
  - It means the very first time you use a Jarvis launcher, that browser
    will open "logged out" / without your usual bookmarks and saved
    logins, like a brand new install. Sign back in once and it'll
    remember it for next time (it's a normal profile, just a separate
    one from your everyday one).
  - If you'd rather it use your REAL existing profile (same logins,
    history, bookmarks as normal), you can edit the .bat and remove the
    --user-data-dir line entirely — but then you must fully close your
    normal browser first every time, with no exceptions, or the flag is
    silently ignored.

ADDING ANOTHER BROWSER LATER
-----------------------------
Every Chromium-based browser works the same way. To add one:
  1. Copy any existing .bat file.
  2. Change the path on the "start" line to that browser's .exe.
  3. Give it a debug port not already used by another launcher here
     (see the top of tab_watcher.py for the current list — pick the
     next free number, e.g. 9226).
  4. Add the (name, port) pair to BROWSER_PORTS in tab_watcher.py.
No other code changes needed — the tracking logic is generic.

NOTE ON FIREFOX
---------------
Firefox is not Chromium-based and does not speak this same protocol, so
it isn't included here. Jarvis will still be able to tell "a browser is
focused" via the general frontmost-app detection, just not which tab.
