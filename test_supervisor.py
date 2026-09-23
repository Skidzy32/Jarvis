"""
Real-execution test for jarvis_supervisor.py (2.2.1): real child
processes, real stop/linking behaviour, the real server for the
"end the session before shutting down" case.

Safe to run on the real machine: logs/pid/stop files go to a temporary
folder, the real focus ledger is snapshotted and restored, and the part
that starts a real server is skipped if Jarvis is already running.
Run: python3 test_supervisor.py
"""

import atexit
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

import jarvis_supervisor as sup
import focus_session as fs

PASS = 0
FAIL = 0


def check(label, condition):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK]   {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}")


# Everything the supervisor writes goes to a throwaway folder.
TMP = tempfile.mkdtemp(prefix="jarvis-sup-test-")
sup.LOG_DIR = TMP
sup.PID_FILE = os.path.join(TMP, "jarvis.pid")
sup.STOP_FILE = os.path.join(TMP, "stop.request")
sup.OVERLAY_START_DELAY = 0.2
sup.TERMINATE_GRACE = 3.0

# Protect the real focus ledger (the server test ends a session, which writes to it).
_LEDGER = open(fs.LEDGER_PATH, "rb").read() if os.path.exists(fs.LEDGER_PATH) else None
def _restore():
    if _LEDGER is None:
        if os.path.exists(fs.LEDGER_PATH):
            os.remove(fs.LEDGER_PATH)
    else:
        with open(fs.LEDGER_PATH, "wb") as f:
            f.write(_LEDGER)
atexit.register(_restore)

SLEEPER = [sys.executable, "-c", "import time; print('child up', flush=True); time.sleep(120)"]


def run_in_thread(children, **kw):
    result = {}
    t = threading.Thread(target=lambda: result.setdefault("first", sup.run(children, **kw)), daemon=True)
    t.start()
    return t, result


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def pids_from_log():
    """Child pids from lines like '...  started server (pid 123)' -- not
    the supervisor's own 'supervisor started (pid ...)' line."""
    import re
    text = open(os.path.join(TMP, "supervisor.log")).read()
    return {m.group(1): int(m.group(2)) for m in re.finditer(r"  started (\w+) \(pid (\d+)\)", text)}


print("Test 1: one child stops -> the supervisor stops the other and exits")
open(os.path.join(TMP, "supervisor.log"), "w").close()
t, result = run_in_thread([("server", SLEEPER, 0), ("overlay", SLEEPER, 0.2)], server_url="http://localhost:1")
time.sleep(1.5)
pids = pids_from_log()
check("both children started", set(pids) == {"server", "overlay"} and all(alive(p) for p in pids.values()))
check("pid file written while running", os.path.exists(sup.PID_FILE) and open(sup.PID_FILE).read().strip() == str(os.getpid()))
os.kill(pids["overlay"], 9)          # the card closes (or crashes)
t.join(timeout=12)
check("supervisor exited on its own", not t.is_alive())
check("it reports the card as the one that stopped first", result.get("first") == "overlay")
time.sleep(0.3)
check("and the server was stopped too", not alive(pids["server"]))
check("pid file removed on the way out", not os.path.exists(sup.PID_FILE))
check("the child's own output reached its log file", "child up" in open(os.path.join(TMP, "server.log")).read())

print("\nTest 2: the other way round -- the server stops -> the card is stopped")
open(os.path.join(TMP, "supervisor.log"), "w").close()
t, result = run_in_thread([("server", SLEEPER, 0), ("overlay", SLEEPER, 0.2)], server_url="http://localhost:1")
time.sleep(1.5)
pids = pids_from_log()
os.kill(pids["server"], 9)
t.join(timeout=12)
time.sleep(0.3)
check("server reported first", result.get("first") == "server")
check("card stopped too", not alive(pids["overlay"]))

print("\nTest 3: Stop Jarvis.bat's stop request -> clean shutdown")
open(os.path.join(TMP, "supervisor.log"), "w").close()
t, result = run_in_thread([("server", SLEEPER, 0), ("overlay", SLEEPER, 0.2)], server_url="http://localhost:1")
time.sleep(1.5)
pids = pids_from_log()
open(sup.STOP_FILE, "w").write("stop")
t.join(timeout=12)
time.sleep(0.3)
check("supervisor exited", not t.is_alive() and result.get("first") == "stop request")
check("both children stopped", not any(alive(p) for p in pids.values()))
check("the request file was cleared", not os.path.exists(sup.STOP_FILE))
check("pid file removed", not os.path.exists(sup.PID_FILE))

print("\nTest 4: a stale stop request left from last time doesn't stop a new start")
open(sup.STOP_FILE, "w").write("stop")
open(os.path.join(TMP, "supervisor.log"), "w").close()
t, result = run_in_thread([("server", SLEEPER, 0), ("overlay", SLEEPER, 0.2)], server_url="http://localhost:1")
time.sleep(2.5)
check("still running 2.5s later", t.is_alive())
pids = pids_from_log()
os.kill(pids["overlay"], 9)
t.join(timeout=12)

print("\nTest 5: pythonw -> python for the children (so their output reaches the logs)")
fake = tempfile.mkdtemp()
for n in ("python.exe", "pythonw.exe"):
    open(os.path.join(fake, n), "w").close()
real_exe = sys.executable
sys.executable = os.path.join(fake, "pythonw.exe")
check("pythonw.exe -> the python.exe next to it", sup.console_python() == os.path.join(fake, "python.exe"))
sys.executable = real_exe
check("anything else is used as-is", sup.console_python() == real_exe)

print("\nTest 6: the real server -- quitting mid-session ends the session properly first")
if sup.server_is_up():
    print("  [SKIP] a Jarvis server is already running here; not touching it")
else:
    check("--is-running says no before start", sup.main(["--is-running"]) == 1)
    before = open(fs.LEDGER_PATH, "rb").read() if os.path.exists(fs.LEDGER_PATH) else b""
    open(os.path.join(TMP, "supervisor.log"), "w").close()
    server_cmd = [sys.executable, "-u", os.path.join(sup.ROOT, "server.py")]
    t, result = run_in_thread([("server", server_cmd, 0), ("overlay", SLEEPER, 0.2)])
    check("--wait sees it come up", sup.main(["--wait", "15"]) == 0)
    check("--is-running now says yes", sup.main(["--is-running"]) == 0)
    body = json.dumps({"message": "start a focus session on the supervisor test for 5 minutes"}).encode()
    urllib.request.urlopen(urllib.request.Request("http://localhost:4700/focus/command", data=body,
                           headers={"Content-Type": "application/json"}, method="POST"), timeout=3).read()
    time.sleep(1)
    pids = pids_from_log()
    os.kill(pids["overlay"], 9)       # close the card mid-session
    t.join(timeout=20)
    time.sleep(0.5)
    after = open(fs.LEDGER_PATH, "rb").read() if os.path.exists(fs.LEDGER_PATH) else b""
    entries = json.loads(after.decode() or "[]")
    check("the running session was saved to the ledger before shutdown", len(after) > len(before) and entries and entries[-1]["completed"] is False)
    check("supervisor log records it", "ended the running focus session" in open(os.path.join(TMP, "supervisor.log")).read())
    check("the server is gone", not sup.server_is_up())
    check("--tail shows the server's log", "Jarvis foundation running" in sup.tail(os.path.join(TMP, "server.log")))

print("\nTest 7: which addresses count as Jarvis's tab")
import tab_watcher as tw
for url, want in [("http://localhost:4700/", True), ("http://localhost:4700/?mute=1", True),
                  ("http://127.0.0.1:4700/notes", True), ("http://LOCALHOST:4700", True),
                  ("http://localhost:4701/", False), ("https://www.youtube.com/watch?v=localhost:4700", False),
                  ("http://evil.example/localhost:4700", False), ("chrome://newtab/", False), ("", False), (None, False)]:
    check(f"{url!r} -> {want}", tw.is_jarvis_url(url) == want)

print("\nTest 8: closing Jarvis closes its tab in a tab-tracking browser (real Chromium)")
CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
TEST_PORT = 9299   # a port no real browser launcher uses, so real tabs are never touched
if not os.path.exists(CHROME):
    print("  [SKIP] no test Chromium on this machine (this part runs in the dev sandbox)")
elif sup.server_is_up():
    print("  [SKIP] a Jarvis server is already running here; not touching it")
else:
    import http.server, socketserver
    class _Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a): pass
    other = socketserver.TCPServer(("127.0.0.1", 0), _Quiet)
    other_url = f"http://127.0.0.1:{other.server_address[1]}/"
    threading.Thread(target=other.serve_forever, daemon=True).start()
    prof = tempfile.mkdtemp(prefix="jarvis-cdp-")
    chrome = subprocess.Popen([CHROME, "--headless=new", "--no-sandbox", f"--remote-debugging-port={TEST_PORT}",
                               f"--user-data-dir={prof}", "--no-proxy-server", "about:blank"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    real_ports = tw.BROWSER_PORTS
    tw.BROWSER_PORTS = [("Test Chromium", TEST_PORT)]
    def tabs():
        return [x.get("url", "") for x in tw._fetch_json(f"http://127.0.0.1:{TEST_PORT}/json/list", timeout=2)
                if x.get("type") == "page"]
    def open_tab(url):
        urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{TEST_PORT}/json/new?{url}", method="PUT"), timeout=3).read()
    try:
        for _ in range(40):
            try:
                tabs(); break
            except Exception:
                time.sleep(0.25)
        server_cmd = [sys.executable, "-u", os.path.join(sup.ROOT, "server.py")]

        # 8a: Stop Jarvis.bat -> Jarvis tab closed, the other tab left alone
        open(os.path.join(TMP, "supervisor.log"), "w").close()
        t, result = run_in_thread([("server", server_cmd, 0), ("overlay", SLEEPER, 0.2)])
        check("server came up", sup.main(["--wait", "15"]) == 0)
        open_tab("http://localhost:4700/?mute=1"); open_tab(other_url)
        time.sleep(2)
        check("both tabs open before stopping", any(tw.is_jarvis_url(u) for u in tabs()) and other_url in tabs())
        open(sup.STOP_FILE, "w").write("stop")
        t.join(timeout=20)
        time.sleep(1)
        after = tabs()
        check("Stop Jarvis.bat closed the Jarvis tab", not any(tw.is_jarvis_url(u) for u in after))
        check("and left the other tab alone", other_url in after)
        check("supervisor log records it", "closed 1 Jarvis browser tab(s)" in open(os.path.join(TMP, "supervisor.log")).read())

        # 8b: closing the card does the same
        t, result = run_in_thread([("server", server_cmd, 0), ("overlay", SLEEPER, 0.2)])
        sup.main(["--wait", "15"])
        open_tab("http://localhost:4700/")
        time.sleep(2)
        os.kill(pids_from_log()["overlay"], 9)
        t.join(timeout=20)
        time.sleep(1)
        check("closing the card closed the Jarvis tab", not any(tw.is_jarvis_url(u) for u in tabs()))

        # 8c: the server stopping by itself leaves the tab open (so it can say so)
        open(os.path.join(TMP, "supervisor.log"), "w").close()
        t, result = run_in_thread([("server", server_cmd, 0), ("overlay", SLEEPER, 0.2)])
        sup.main(["--wait", "15"])
        open_tab("http://localhost:4700/")
        time.sleep(2)
        os.kill(pids_from_log()["server"], 9)
        t.join(timeout=20)
        time.sleep(1)
        check("a server crash leaves the tab open", any(tw.is_jarvis_url(u) for u in tabs()))
    finally:
        tw.BROWSER_PORTS = real_ports
        chrome.kill(); chrome.wait()
        other.shutdown()

print(f"\n{PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)
