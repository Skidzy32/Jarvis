"""
4.3.0: the launcher (what the Jarvis icon runs), the boot hand-over, and
Quit. Runs the real start-up path on this machine, then quits it.
Run: python3 test_launcher.py   (Jarvis must NOT already be running)
"""
import importlib.machinery, importlib.util, json, os, shutil, subprocess, sys, tempfile, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec); loader.exec_module(mod); return mod
spec = None
L = load("jarvis_launcher", os.path.join(HERE, "jarvis_launcher.pyw"))

PASS = FAIL = 0
def check(label, ok):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
    print(f"  [{'OK' if ok else 'FAIL'}]{'   ' if ok else ' '}{label}")

print("Test 1: which browser opens the app window")
L.IS_WINDOWS, L.IS_MAC = True, False
have = {r"C:\PF\Microsoft\Edge\Application\msedge.exe", r"C:\LA\Programs\Opera GX\opera.exe"}
exp = lambda p: p.replace("%ProgramFiles(x86)%", r"C:\PF").replace("%ProgramFiles%", r"C:\PF").replace("%LOCALAPPDATA%", r"C:\LA")
pick = lambda pref: L.find_browser(pref, exists=lambda p: p in have, expand=exp)
check("auto: the first one installed (Chrome missing -> Edge)", pick("auto")[0] == "edge")
check("a preference wins when installed (Opera GX)", pick("opera")[1] == "Opera GX" and pick("opera")[2] == 9222)
check("a preference that isn't installed falls back", pick("brave")[0] == "edge")
check("'default' -> your normal browser", pick("default") is None)
check("nothing installed -> normal browser", L.find_browser("auto", exists=lambda p: False, expand=exp) is None)
L.IS_WINDOWS, L.IS_MAC = False, True
check("Mac: finds Chrome in Applications", L.find_browser("auto", exists=lambda p: p.startswith("/Applications/Google Chrome"))[0] == "chrome")
L.IS_WINDOWS = L.IS_MAC = False
cmd = L.app_window_command(("chrome", "Chrome", 9223, "chrome.exe"), "file:///x/boot.html")
check("app window: no tabs/address bar, tab tracking port, Jarvis's own profile",
      cmd[1] == "--app=file:///x/boot.html" and "--remote-debugging-port=9223" in cmd
      and any(c.startswith("--user-data-dir=") and c.endswith(os.path.join("jarvis-profiles", "chrome")) for c in cmd))

print("Test 2: never starts Jarvis twice")
L.LOCK = os.path.join(tempfile.mkdtemp(), "launcher.lock")
check("no lock -> free to start", not L.start_is_under_way())
open(L.LOCK, "w").write("x")
check("a start in the last 25 s -> don't start another", L.start_is_under_way())
check("an old lock doesn't block", not L.start_is_under_way(now=time.time() + 60))
calls = []
L.is_running, L.start_jarvis = (lambda: True), (lambda: calls.append("start"))
L.open_window = lambda url: calls.append(url)
L.main()
check("already running -> straight to Jarvis, nothing started", calls == [L.APP_URL])
calls.clear(); L.is_running = lambda: False; L.LOCK = os.path.join(tempfile.mkdtemp(), "none.lock")
L.main()
check("not running -> starts it, opens the boot screen", calls[0] == "start" and calls[1].startswith("file://") and calls[1].endswith("viewer/boot.html"))

print("Test 3: the real thing, on this machine (a copy, so nothing here is touched)")
W = os.path.join(tempfile.mkdtemp(), "jarvis")
shutil.copytree(HERE, W, ignore=shutil.ignore_patterns("notes", "usage", "logs", "config.json", "__pycache__", ".git", "*.log",
                                                      "focus_ledger.json", "review_settings.json", "overlay_position.json"))
L2 = load("jl2", os.path.join(W, "jarvis_launcher.pyw"))
opened = []
L2.webbrowser.open = lambda url: opened.append(url) or True
L2.IS_WINDOWS = False
if L2.is_running():
    check("SKIPPED: something is already running on port 4700", False)
else:
    L2.main()
    up = False
    for _ in range(60):
        time.sleep(0.25)
        if L2.is_running(): up = True; break
    check("Jarvis started in the background", up)
    check("the boot screen was opened", opened and opened[0] == L2.APP_URL + "boot.html")
    body = urllib.request.urlopen(L2.APP_URL + "setup/state", timeout=3).read()
    check("a fresh copy is a first run (setup screens will show)", json.loads(body)["first_run"] is True)
    req = urllib.request.Request(L2.APP_URL + "setup/save", data=json.dumps({"app_browser": "brave"}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    check("Settings: browser choice saved for the icon", json.loads(urllib.request.urlopen(req).read())["app_browser"] == "brave"
          and L2.preference() == "brave")
    req = urllib.request.Request(L2.APP_URL + "setup/save", data=b'{"app_browser": "netscape"}',
                                 headers={"Content-Type": "application/json"}, method="POST")
    check("an unknown browser is refused", json.loads(urllib.request.urlopen(req).read())["ok"] is False)
    L2.main()
    check("second click while running: straight in, no second start", opened[-1] == L2.APP_URL)
    req = urllib.request.Request(L2.APP_URL + "system/quit", data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
    r = json.loads(urllib.request.urlopen(req, timeout=3).read())
    time.sleep(1.5)
    check("Quit Jarvis: answers, then really stops", r["ok"] and not L2.is_running())
shutil.rmtree(os.path.dirname(W), ignore_errors=True)
print(f"\n{PASS} passed, {FAIL} failed")
