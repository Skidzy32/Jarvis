"""
jarvis_supervisor.py — runs Jarvis's two background processes with NO
console windows (2.2.1).

Start Jarvis.bat launches this with pythonw.exe (Python's windowless
interpreter), so nothing appears on screen except the browser and the small
countdown card. This file then:

  1. starts server.py and focus_overlay.py as child processes, each with no
     console window, their output going to logs/server.log and
     logs/overlay.log (so errors can still be read — nothing is silently
     thrown away);
  2. keeps them linked, the job jarvis_watchdog.bat used to do by window
     title (there are no windows now): the moment either one stops — closed
     from the card's ✕, crashed, or killed — the other is stopped too;
  3. before stopping the server, ends a running focus session properly
     ("stop my focus session"), so quitting Jarvis mid-session still gives
     you the report card and the ledger entry instead of losing the session;
  4. when closed on purpose (card or Stop Jarvis.bat), closes Jarvis's
     browser tab too, in any browser started with tab tracking (2.2.2);
  5. writes its process id to logs/jarvis.pid, and shuts down the same
     careful way when Stop Jarvis.bat leaves a logs/stop.request file (the
     batch file only force-kills if this doesn't respond).

Only standard library. The process logic is tested for real in the dev
sandbox (test_supervisor.py); the "no window" flag itself only means
anything on Windows.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(ROOT, "logs")
PID_FILE = os.path.join(LOG_DIR, "jarvis.pid")
# Stop Jarvis.bat creates this; the supervisor sees it within a second and
# shuts down the normal way (ending a running session first).
STOP_FILE = os.path.join(LOG_DIR, "stop.request")
SERVER_URL = "http://localhost:4700"

POLL_SECONDS = 1.0            # how often the children are checked
OVERLAY_START_DELAY = 2.0     # let the server bind its port before the card starts
STOP_SESSION_TIMEOUT = 3.0    # how long to wait for the server to end a session on quit
TERMINATE_GRACE = 5.0         # how long a child gets to exit before it's killed

# On Windows, don't give child processes a console window of their own.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def console_python():
    """The console interpreter next to whatever is running this file.
    Children use python.exe (with no window) rather than pythonw.exe so
    their print() output reliably reaches the log files."""
    exe = sys.executable
    folder, name = os.path.split(exe)
    if name.lower() == "pythonw.exe":
        candidate = os.path.join(folder, "python.exe")
        if os.path.exists(candidate):
            return candidate
    return exe


def default_children():
    py = console_python()
    return [
        ("server", [py, "-u", os.path.join(ROOT, "server.py")], 0.0),
        ("overlay", [py, "-u", os.path.join(ROOT, "focus_overlay.py")], OVERLAY_START_DELAY),
    ]


def _log(message):
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(os.path.join(LOG_DIR, "supervisor.log"), "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {message}\n")


def _start(name, cmd):
    os.makedirs(LOG_DIR, exist_ok=True)
    log = open(os.path.join(LOG_DIR, f"{name}.log"), "a", encoding="utf-8")
    log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} starting {name} =====\n")
    log.flush()
    proc = subprocess.Popen(
        cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
    )
    return proc, log


def end_focus_session(server_url=SERVER_URL, timeout=STOP_SESSION_TIMEOUT):
    """Ask the server to end a running focus session the normal way (report
    card + ledger entry). Returns True if one was running and was ended.
    Never raises: if the server is already gone there's nothing to end."""
    try:
        with urllib.request.urlopen(f"{server_url}/focus/status", timeout=timeout) as r:
            active = json.loads(r.read().decode("utf-8")).get("status", {}).get("active")
        if not active:
            return False
        body = json.dumps({"message": "stop my focus session"}).encode("utf-8")
        req = urllib.request.Request(f"{server_url}/focus/command", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout):
            pass
        return True
    except Exception:
        return False


# Which "first to stop" reasons count as the user closing Jarvis on purpose.
DELIBERATE_STOPS = ("overlay", "stop request")


def close_browser_tabs():
    """Close Jarvis's tab in any tab-tracking browser. Never raises."""
    try:
        import tab_watcher
        return tab_watcher.close_jarvis_tabs()
    except Exception:
        return 0


def _stop(proc):
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=TERMINATE_GRACE)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def run(children=None, server_url=SERVER_URL, write_pid=True):
    """Start the children, then watch them until one stops; then stop the
    rest (ending any focus session first) and return the name of the one
    that stopped first."""
    children = children or default_children()
    running = []  # (name, proc, logfile)
    os.makedirs(LOG_DIR, exist_ok=True)
    if os.path.exists(STOP_FILE):
        os.remove(STOP_FILE)  # a stale request from last time must not stop us now
    if write_pid:
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
    _log(f"supervisor started (pid {os.getpid()})")
    first_down = None
    try:
        for name, cmd, delay in children:
            if delay:
                time.sleep(delay)
            proc, log = _start(name, cmd)
            running.append((name, proc, log))
            _log(f"started {name} (pid {proc.pid})")

        while first_down is None:
            if os.path.exists(STOP_FILE):
                first_down = "stop request"
                _log("Stop Jarvis.bat asked to stop")
                try:
                    os.remove(STOP_FILE)
                except OSError:
                    pass
                break
            for name, proc, _log_file in running:
                if proc.poll() is not None:
                    first_down = name
                    _log(f"{name} stopped (exit code {proc.returncode}) — stopping the rest")
                    break
            else:
                time.sleep(POLL_SECONDS)
    finally:
        if end_focus_session(server_url):
            _log("ended the running focus session before shutting down")
        # 2.2.2: closing Jarvis on purpose (card's x or Stop Jarvis.bat)
        # closes its browser tab too. If the server stopped by itself,
        # the tab is left open so its "Jarvis has stopped" screen shows.
        if first_down in DELIBERATE_STOPS:
            n = close_browser_tabs()
            if n:
                _log(f"closed {n} Jarvis browser tab(s)")
        for name, proc, log in running:
            _stop(proc)
            log.close()
        if write_pid and os.path.exists(PID_FILE):
            try:
                with open(PID_FILE) as f:
                    if f.read().strip() == str(os.getpid()):
                        os.remove(PID_FILE)
            except OSError:
                pass
        _log("supervisor finished")
    return first_down


def server_is_up(server_url=SERVER_URL, timeout=1.0):
    try:
        with urllib.request.urlopen(f"{server_url}/model", timeout=timeout):
            return True
    except Exception:
        return False


def wait_until_up(seconds, server_url=SERVER_URL):
    end = time.time() + seconds
    while time.time() < end:
        if server_is_up(server_url):
            return True
        time.sleep(0.5)
    return False


def tail(path, lines=20):
    if not os.path.exists(path):
        return f"(no {os.path.basename(path)} yet)"
    with open(path, encoding="utf-8", errors="replace") as f:
        return "".join(f.readlines()[-lines:]).rstrip()


def main(argv):
    """Small helpers for Start Jarvis.bat, so the launcher window can show
    real answers without fragile batch logic:
        --is-running     exit 0 if Jarvis already answers, else 1
        --wait N         exit 0 once it answers (up to N seconds), else 1
        --tail           print the end of logs/server.log
        (no arguments)   run the supervisor itself"""
    if "--is-running" in argv:
        return 0 if server_is_up() else 1
    if "--wait" in argv:
        i = argv.index("--wait")
        seconds = float(argv[i + 1]) if i + 1 < len(argv) else 20
        return 0 if wait_until_up(seconds) else 1
    if "--tail" in argv:
        print(tail(os.path.join(LOG_DIR, "server.log")))
        return 0
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
