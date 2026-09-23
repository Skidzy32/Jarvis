"""
jarvis_launcher.pyw — 4.3.0: what the Jarvis icon runs.

Double-clicking the Jarvis icon (Desktop, Start menu, or Applications on a
Mac) runs this with Python's windowless interpreter, so there are no console
windows. It:
  1. starts Jarvis in the background if it isn't already running
     (Windows: through jarvis_supervisor.py, which also runs the countdown
     card; Mac: the server on its own);
  2. opens Jarvis in its own app window -- no tabs, no address bar -- starting
     on the boot screen (viewer/boot.html), which waits for Jarvis to answer
     and then hands over. If Jarvis is already running it opens straight in.

The app window is Chrome, Edge, Brave or Opera GX in "app mode", using
Jarvis's own browser profile with tab tracking switched on (the same ports
as browser_launchers/*.bat), so focus sessions can see which site you're on
in that browser. Which browser: ⚙ Settings in Jarvis ("app_browser" in
config.json): auto (the first one found), chrome, edge, brave, opera, or
default (your normal browser, as a tab).

Standard library only.
"""

import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
APP_URL = "http://localhost:4700/"
BOOT_FILE = os.path.join(ROOT, "viewer", "boot.html")
LOCK = os.path.join(ROOT, "logs", "launcher.lock")
START_GRACE_S = 25          # a start already under way: don't start a second one
IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# (key, name, tab-tracking port, Windows paths, Mac app bundle)
BROWSERS = [
    ("chrome", "Chrome", 9223,
     [r"%ProgramFiles%\Google\Chrome\Application\chrome.exe", r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
      r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"],
     "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    ("edge", "Edge", 9224,
     [r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe", r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"],
     "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    ("brave", "Brave", 9225,
     [r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe",
      r"%ProgramFiles(x86)%\BraveSoftware\Brave-Browser\Application\brave.exe",
      r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"],
     "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
    ("opera", "Opera GX", 9222,
     [r"%LOCALAPPDATA%\Programs\Opera GX\opera.exe", r"%LOCALAPPDATA%\Programs\Opera GX\launcher.exe"],
     "/Applications/Opera GX.app/Contents/MacOS/Opera"),
]
APP_BROWSERS = ("auto", "chrome", "edge", "brave", "opera", "default")


def log(msg):
    try:
        os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
        with open(os.path.join(ROOT, "logs", "launcher.log"), "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
    except OSError:
        pass


def preference():
    try:
        with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
            p = json.load(f).get("app_browser", "auto")
        return p if p in APP_BROWSERS else "auto"
    except (OSError, ValueError):
        return "auto"


def find_browser(pref, exists=os.path.exists, expand=os.path.expandvars):
    """-> (key, name, port, executable) for the chosen/first available
    browser, or None (then the default browser is used, as a tab)."""
    if pref == "default":
        return None
    order = [b for b in BROWSERS if b[0] == pref] + [b for b in BROWSERS if b[0] != pref]
    for key, name, port, win_paths, mac_path in order:
        paths = [expand(p) for p in win_paths] if IS_WINDOWS else [mac_path] if IS_MAC else []
        for p in paths:
            if exists(p):
                return key, name, port, p
    return None


def available_browsers(exists=os.path.exists, expand=os.path.expandvars):
    """[(key, name)] of the app-window browsers installed here, in order."""
    out = []
    for key, name, _port, win_paths, mac_path in BROWSERS:
        paths = [expand(p) for p in win_paths] if IS_WINDOWS else [mac_path] if IS_MAC else []
        if any(exists(p) for p in paths):
            out.append((key, name))
    return out


def app_window_command(browser, url):
    key, _name, port, exe = browser
    profile = os.path.join(ROOT, "browser_launchers", "jarvis-profiles", key)
    return [exe, f"--app={url}", f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
            "--window-size=1440,900", "--no-first-run", "--no-default-browser-check"]


def is_running(timeout=1.0):
    try:
        with urllib.request.urlopen(APP_URL + "model", timeout=timeout):
            return True
    except Exception:
        return False


def start_is_under_way(now=None):
    try:
        return (now or time.time()) - os.path.getmtime(LOCK) < START_GRACE_S
    except OSError:
        return False


def start_jarvis():
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    pathlib.Path(LOCK).write_text(str(time.time()), encoding="utf-8")
    if IS_WINDOWS:
        flags = 0x00000008 | 0x08000000          # DETACHED_PROCESS | CREATE_NO_WINDOW
        subprocess.Popen([sys.executable, os.path.join(ROOT, "jarvis_supervisor.py")], cwd=ROOT,
                         creationflags=flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True)
    else:
        out = open(os.path.join(ROOT, "logs", "server.log"), "a", encoding="utf-8")
        subprocess.Popen([sys.executable, "-u", os.path.join(ROOT, "server.py")], cwd=ROOT,
                         stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    log("started Jarvis")


def open_window(url):
    browser = find_browser(preference())
    if browser:
        try:
            kw = {"creationflags": 0x00000008} if IS_WINDOWS else {"start_new_session": True}
            subprocess.Popen(app_window_command(browser, url), stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
            log(f"opened {browser[1]} app window")
            return browser[1]
        except OSError as e:
            log(f"couldn't open {browser[1]}: {e}")
    # 4.4.0: your normal browser. A file:// page can be handed to whatever
    # opens .html FILES (not always your browser), so wait for Jarvis and
    # open a normal web address instead, which always goes to your browser.
    if url.startswith("file:"):
        for _ in range(100):
            if is_running(0.5):
                break
            time.sleep(0.25)
        url = APP_URL + "boot.html"
    webbrowser.open(url)
    log("opened the default browser")
    return "default"


def main():
    if is_running():
        return open_window(APP_URL)
    if not start_is_under_way():
        start_jarvis()
    return open_window(pathlib.Path(BOOT_FILE).as_uri())


if __name__ == "__main__":
    try:
        main()
    except Exception as e:      # windowless: never fail silently
        log(f"launcher error: {e!r}")
        raise
