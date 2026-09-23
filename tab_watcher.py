"""
tab_watcher.py — browser-agnostic active-tab reader.

Any Chromium-based browser (Chrome, Edge, Opera, Opera GX, Brave, Vivaldi...)
exposes the same "Chrome DevTools Protocol" (CDP) debugging endpoint when
launched with a --remote-debugging-port=<port> flag. This module doesn't
care which specific browser is running — it just asks a known list of
ports "is anyone listening, and if so what's the active tab?" and returns
the first real answer it finds.

This is what makes future-proofing possible: adding a new browser later is
just adding one more (name, port) pair to BROWSER_PORTS, no code changes.

Nothing here is Windows-specific. It's plain HTTP + JSON against localhost,
so it has been run and verified for real in the dev sandbox against a
real Chromium instance (see the bottom of this file / preflight.py).
"""

import json
import urllib.request
import urllib.error

# One dedicated port per browser so more than one can (in theory) run with
# debugging enabled at once without colliding. Matches the ports baked
# into browser_launchers/*.bat — keep these in sync if you add a browser.
BROWSER_PORTS = [
    ("Opera GX", 9222),
    ("Chrome", 9223),
    ("Edge", 9224),
    ("Brave", 9225),
]

REQUEST_TIMEOUT = 0.6  # seconds — this is polled frequently, fail fast


def _fetch_json(url, timeout=REQUEST_TIMEOUT):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _active_tab_from_targets(targets):
    """
    /json/list returns every open tab/devtools target. There's no direct
    "which one is focused" flag in the plain list endpoint, so we use the
    practical heuristic: the first 'page' type target that isn't a
    devtools/extension internal page. Chromium orders this list with the
    most-recently-active tab first in every version we've checked, which
    is the same assumption Chrome extensions like "Active Tab" rely on.
    """
    for t in targets:
        if t.get("type") == "page" and not t.get("url", "").startswith(
            ("devtools://", "chrome://", "chrome-extension://")
        ):
            return {
                "title": t.get("title", "").strip(),
                "url": t.get("url", ""),
            }
    # fall back to literally anything if every tab was internal
    if targets:
        t = targets[0]
        return {"title": t.get("title", "").strip(), "url": t.get("url", "")}
    return None


def get_active_tab():
    """
    Returns {"browser": <name>, "title": ..., "url": ...} for the first
    browser found listening on any known debug port, or None if no
    Chromium-based browser currently has remote debugging enabled
    (e.g. the user launched their browser normally, without one of the
    browser_launchers/*.bat shortcuts).
    """
    for name, port in BROWSER_PORTS:
        try:
            targets = _fetch_json(f"http://127.0.0.1:{port}/json/list")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            continue
        tab = _active_tab_from_targets(targets)
        if tab:
            tab["browser"] = name
            tab["port"] = port
            return tab
    return None


def any_debug_browser_running():
    """Quick boolean check, used to decide whether to show a 'no tab
    tracking available right now' hint instead of silently doing nothing."""
    return get_active_tab() is not None


JARVIS_HOSTS = ("localhost:4700", "127.0.0.1:4700")


def is_jarvis_url(url):
    """True for Jarvis's own page (any path on the Jarvis server)."""
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(url or "")
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and parts.netloc.lower() in JARVIS_HOSTS


def close_jarvis_tabs(timeout=REQUEST_TIMEOUT):
    """
    2.2.2: used when Jarvis shuts down. Closes every Jarvis tab in any
    browser started through browser_launchers (the ones with a debug port),
    and nothing else -- only tabs whose address is the Jarvis server.
    Returns how many were closed. Never raises.

    A tab in a browser opened normally (menu option 5) has no debug port,
    so it can't be closed from outside; that page shows "Jarvis has
    stopped" by itself instead (viewer/index.html).
    """
    closed = 0
    for _name, port in BROWSER_PORTS:
        try:
            targets = _fetch_json(f"http://127.0.0.1:{port}/json/list", timeout=timeout)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            continue
        for t in targets:
            if t.get("type") == "page" and is_jarvis_url(t.get("url")) and t.get("id"):
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/json/close/{t['id']}", timeout=timeout
                    ):
                        closed += 1
                except (urllib.error.URLError, TimeoutError, OSError):
                    pass
    return closed


if __name__ == "__main__":
    result = get_active_tab()
    if result:
        print(f"[{result['browser']}] {result['title']}  ->  {result['url']}")
    else:
        print("No Chromium-based browser found with remote debugging enabled.")
        print("Launch one via a browser_launchers/*.bat shortcut and try again.")
