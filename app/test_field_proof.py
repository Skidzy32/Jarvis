"""Prompt 16 (3.4.0): /focus/diag -- flags only, read from the running
server. Uses fake readers; the real focus ledger is never touched."""
import json, os, tempfile, threading, time, urllib.request
from http.server import ThreadingHTTPServer

import focus_session as fs

PASSED = FAILED = 0
def check(name, ok):
    global PASSED, FAILED
    PASSED += bool(ok); FAILED += (not ok)
    print(("PASS " if ok else "FAIL ") + name)

REAL_LEDGER = fs.LEDGER_PATH
read = lambda p: open(p, "rb").read() if os.path.exists(p) else None
BEFORE = read(REAL_LEDGER)
fs.LEDGER_PATH = os.path.join(tempfile.mkdtemp(), "ledger.json")   # never the real one
fs.TICK_SECONDS = 0.05

state = {"window": None, "tab": None}
win = lambda: state["window"]
tab = lambda: state["tab"]
SECRET = ("instagram", "youtube", "chrome.exe", "discord", "Secret Title", "example.org")

def leaks(d):
    text = json.dumps(d).lower()
    return [s for s in SECRET if s.lower() in text]

# nothing readable, no session
d = fs.diag(tab, win)
check("idle: nothing readable, no session", not d["frontmost_readable"] and not d["session_on"]
      and not d["tick_thread_alive"] and d["tab_read"] == "no_window_reader")

# start from the Jarvis tab -> deferred
state["window"] = {"process": "chrome.exe", "title": "Secret Title"}
state["tab"] = {"browser": "Chrome", "url": "http://localhost:4700/"}
fs.start_session("essay", 5, get_tab_fn=tab, get_window_fn=win)
d = fs.diag(tab, win)
check("deferred from Jarvis tab", d["deferred"] and d["front_is_jarvis"] and d["session_on"]
      and d["tick_thread_alive"] and not d["has_tab_target"] and d["frontmost_is_browser"] and d["tab_read"] == "read")

# go to a work tab; after settling it locks
state["tab"] = {"browser": "Chrome", "url": "https://example.org/doc"}
time.sleep(0.4)
d = fs.diag(tab, win)
check("settled: tab + app target, on target both lanes",
      not d["deferred"] and d["has_tab_target"] and d["has_app_target"] and d["on_target_tab"] and d["on_target_app"])

# another tab in the same browser: app lane yes, tab lane no
state["tab"] = {"browser": "Chrome", "url": "https://www.instagram.com/reel/1"}
d = fs.diag(tab, win)
check("other tab: app lane on, tab lane off", d["on_target_app"] and not d["on_target_tab"] and d["hash_present"])

# a different app
state["window"] = {"process": "discord.exe", "title": "Secret Title"}
d = fs.diag(tab, win)
check("other app: both lanes off, not a browser", not d["on_target_app"] and not d["on_target_tab"]
      and not d["frontmost_is_browser"] and d["tab_read"] == "not_a_browser")

# browser whose tabs we can't read
state["window"] = {"process": "brave.exe", "title": "x"}
check("unreadable browser tab reported", fs.diag(tab, win)["tab_read"] == "unreadable")

# every value is a bool, a number, or a known status -- never a name
allowed_status = {"read", "unreadable", "not_a_browser", "tab_only", "no_window_reader"}
d = fs.diag(tab, win)
check("values are flags/counts/status only", all(isinstance(v, (bool, int)) or (k == "tab_read" and v in allowed_status)
                                                for k, v in d.items()))
check("no identities in diag", not leaks(d))

# served by a running server
import server
state["window"] = {"process": "chrome.exe", "title": "Secret Title"}
state["tab"] = {"browser": "Chrome", "url": "https://www.youtube.com/watch?v=1"}
server.tab_watcher.get_active_tab = tab
server.windows_focus.get_frontmost_window = win
httpd = ThreadingHTTPServer(("localhost", 0), server.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
body = urllib.request.urlopen(f"http://localhost:{httpd.server_port}/focus/diag").read().decode()
served = json.loads(body)
check("GET /focus/diag from running server", served["session_on"] and served["tick_thread_alive"])
check("served diag has no identities", not leaks(body))
httpd.shutdown()

fs.stop_session()
time.sleep(0.2)
check("tick thread gone after stop", not fs.diag(tab, win)["tick_thread_alive"])
check("real ledger untouched", read(REAL_LEDGER) == BEFORE)
print(f"{PASSED} passed, {FAILED} failed")
