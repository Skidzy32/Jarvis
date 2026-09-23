"""
focus_overlay.py — true OS-level always-on-top desktop overlay for the
active focus session (Prompt 09, per your choice of a real overlay window
rather than an in-browser card).

This is a SEPARATE PROCESS from server.py, on purpose: an always-on-top
window has to be its own top-level OS window to actually float over other
apps (a browser tab can never do that, no matter how the page is styled).
It talks to server.py purely over HTTP, polling GET /focus/status the
same way the browser viewer does — no special coupling, no shared memory.

Run it alongside the server:
    python3 server.py          (in one window/terminal)
    python3 focus_overlay.py   (in another)
Closing the overlay window does not stop your focus session — it's just a
window onto state that lives in server.py. Re-run this file any time to
bring it back.

PROMPT 11 (2.1.0): the card now has PAUSE/RESUME, LOCK THIS TAB and ABORT
controls. Every button sends the same phrase a person would say, through
the same /focus/command route, flagged from_card=true -- see post_command()
below. Clicking the card makes the card itself the frontmost app; the
server knows that (the "card trap") and reads the browser's tab directly
instead, so LOCK THIS TAB never locks the card.

WHY THIS CAN'T BE FULLY VERIFIED IN THE DEV SANDBOX:
tkinter isn't installed in this Linux dev sandbox (confirmed: import
tkinter fails, and the package can't be installed here either — the
sandbox's package mirror is blocked). Everything in this file that ISN'T
a Tk call — the HTTP polling, the status formatting, the reconnect/retry
logic — is written to be tested on its own (see test_focus_overlay.py),
and that part IS verified for real. The Tk window itself (creating it,
keeping it on top, updating its labels) needs your first real run on
Windows, where tkinter ships built into python.org's installer by
default (no extra pip install needed for that specific part).
"""

import json
import sys
import time
import urllib.request
import urllib.error

SERVER_URL = "http://localhost:4700"
POLL_MS = 1000

# ---- pure logic: HTTP polling + formatting, testable without Tk --------


def fetch_status(server_url=SERVER_URL, timeout=1.5):
    """
    Returns (status_dict, error_string). Exactly one of the two is
    non-None/non-empty. Never raises — the overlay has to keep running
    and show "can't reach Jarvis" rather than crash if the server isn't
    up yet or was restarted.
    """
    try:
        req = urllib.request.Request(f"{server_url}/focus/status")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data, None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return None, str(exc)


def post_command(message, server_url=SERVER_URL, timeout=2.0):
    """
    Prompt 11: the card's buttons go through the SAME /focus/command route
    as voice and typed chat, with from_card=true so the server (a) takes
    the card-trap path for a re-target and (b) queues the reply for the
    Jarvis tab to speak, since the card has no voice. Returns (data, error);
    never raises.
    """
    try:
        body = json.dumps({"message": message, "from_card": True}).encode("utf-8")
        req = urllib.request.Request(
            f"{server_url}/focus/command", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return None, str(exc)


# The exact messages each card button sends -- the same phrases a person
# would say, so focus_commands.py stays the one place that decides what
# a command means.
CARD_PAUSE_MESSAGE = "pause my focus session"
CARD_RESUME_MESSAGE = "resume my focus session"
CARD_RETARGET_MESSAGE = "lock on this tab"
CARD_ABORT_MESSAGE = "abort focus session"

LOCK_PILL_LABEL = "LOCK THIS TAB"

CLOSE_CONFIRM_TEXT = (
    "Close Jarvis?\n\n"
    "This stops Jarvis in the background. A focus session that's running "
    "will be ended first and saved to your history."
)
FLASH_MS = 1000  # how long the pill shows its result before reverting

# What the LOCK THIS TAB pill flashes for each re-target outcome. The spec
# says it "flashes LOCKED for a second"; it only says LOCKED when it
# actually locked, because a card claiming LOCKED after a refusal would be
# exactly the kind of silent wrong answer the pack warns about.
FLASH_LABELS = {
    "locked": "LOCKED",
    "rearmed": "GO TO IT",
    "blind": "CAN'T SEE",
    "no_session": "NO SESSION",
}


def flash_label(data, error):
    """Pure: what the pill should flash after a re-target request."""
    if error or not data:
        return "OFFLINE"
    return FLASH_LABELS.get(data.get("outcome"), "?")


def card_controls(data, error):
    """
    Pure: the state of the card's three controls for a /focus/status
    response. Returns {"enabled": bool, "pause_label": str,
    "pause_message": str}. Controls are greyed out when there's no session
    to act on (or the server can't be reached).
    """
    status = (data or {}).get("status", {}) if not error else {}
    active = bool(status.get("active"))
    paused = bool(status.get("paused"))
    return {
        "enabled": active,
        "pause_label": "▶ RESUME" if paused else "⏸ PAUSE",
        "pause_message": CARD_RESUME_MESSAGE if paused else CARD_PAUSE_MESSAGE,
    }


def format_time(remaining_seconds):
    m, s = divmod(max(0, int(remaining_seconds)), 60)
    return f"{m:02d}:{s:02d}"


def overlay_text(data, error):
    """
    Pure function: given a /focus/status response (or an error), decide
    exactly what the overlay should display. Kept separate from any Tk
    widget code so it's fully unit-testable.
    Returns (title_line, detail_line, color) where color is one of
    "idle" | "on_track" | "drifted" | "error".
    """
    if error:
        return "Jarvis", "Can't reach the server", "error"

    status = data.get("status", {}) if data else {}
    # Prompt 14 THE FACE: while the screen is watched, the card says so.
    eye = "👁 " if (data or {}).get("watching") else ""
    if not status.get("active"):
        return f"{eye}Jarvis", ("Watching your screen" if eye else "No focus session running"), "idle"

    task = status.get("task") or "your session"
    remaining = format_time(status.get("remaining_seconds", 0))
    if status.get("is_drifted"):
        return f"{eye}⏱ {remaining}", f"Drifted from: {task}", "drifted"
    return f"{eye}⏱ {remaining}", f"On track: {task}", "on_track"


COLORS = {
    "idle": "#8fa3c0",
    "on_track": "#3ddc84",
    "drifted": "#ff6b6b",
    "error": "#ffb020",
}

# ---- 4.1.0: look, place and size ------------------------------------------
# A clear border, a title bar you can drag it by, bottom-right above the
# taskbar by default, and it remembers where you put it. When no focus
# session is running it shrinks to a slim "ready" bar instead of a big box.
THEME = {"bg": "#0d1320", "bar": "#16213a", "border": "#3aa7c9", "text": "#eef4ff", "muted": "#9fb0cc",
         "button": "#22314f", "button_hover": "#2d4270", "danger": "#ff6b6b"}
SIZES = {"full": (300, 132), "idle": (240, 58), "mini": (150, 30)}
EDGE_MARGIN = 16          # from the screen edges
TASKBAR_ALLOWANCE = 56    # keep clear of the Windows taskbar / macOS dock
import os as _os
POSITION_FILE = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "overlay_position.json")


def default_position(w, h, screen_w, screen_h):
    """Bottom-right, above the taskbar."""
    return screen_w - w - EDGE_MARGIN, screen_h - h - TASKBAR_ALLOWANCE


def clamp_position(x, y, w, h, screen_w, screen_h):
    """Keep the whole card on screen (monitors change; saved spots can go stale)."""
    x = min(max(0, int(x)), max(0, screen_w - w))
    y = min(max(0, int(y)), max(0, screen_h - h))
    return x, y


def load_position(path=POSITION_FILE):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return int(d["x"]), int(d["y"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_position(x, y, path=POSITION_FILE):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"x": int(x), "y": int(y)}, f)
        return True
    except OSError:
        return False


def card_mode(color, collapsed):
    """Pure: which size the card should be. 'mini' if you collapsed it,
    'idle' (slim) when there's no session, else 'full'."""
    if collapsed:
        return "mini"
    return "idle" if color in ("idle", "error") else "full"


def idle_line(detail, color):
    """Pure: the slim bar's one line when no session is running."""
    if color == "error":
        return "● Can't reach Jarvis"
    if detail == "Watching your screen":
        return "👁 Watching your screen"
    return "● Ready · no focus session"


# ---- Tk window: only imported/built when this file is actually run -----


def run():
    try:
        import tkinter as tk
    except ImportError:
        print(
            "tkinter isn't available in this Python install.\n"
            "On Windows, reinstalling Python from python.org with the "
            "default options includes it — no separate pip install needed."
        )
        sys.exit(1)

    T = THEME
    root = tk.Tk()
    root.title("Jarvis Focus")
    root.overrideredirect(True)        # our own title bar instead of the system one
    root.attributes("-topmost", True)   # always-on-top, the whole point
    root.attributes("-alpha", 0.96)
    root.configure(bg=T["border"])      # the 1px border is the root showing round the edges

    screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
    state = {"pause_message": CARD_PAUSE_MESSAGE, "collapsed": False, "mode": None,
             "drag": (0, 0), "color": "idle"}
    saved = load_position()
    w0, h0 = SIZES["idle"]
    pos = clamp_position(*(saved or default_position(w0, h0, screen_w, screen_h)), w0, h0, screen_w, screen_h)
    root.geometry(f"{w0}x{h0}+{pos[0]}+{pos[1]}")

    inner = tk.Frame(root, bg=T["bg"])
    inner.pack(fill="both", expand=True, padx=1, pady=1)

    # ---- title bar: grip + name (drag here), collapse, close ------------
    bar = tk.Frame(inner, bg=T["bar"], height=24, cursor="fleur")
    bar.pack(fill="x")
    grip = tk.Label(bar, text="⋮⋮  JARVIS", font=("Segoe UI", 8, "bold"), fg=T["muted"], bg=T["bar"], cursor="fleur")
    grip.pack(side="left", padx=(8, 0), pady=3)
    mini_label = tk.Label(bar, text="", font=("Segoe UI", 9, "bold"), fg=T["text"], bg=T["bar"])
    mini_label.pack(side="left", padx=(6, 0))

    def bar_button(text, hover_fg):
        b = tk.Label(bar, text=text, font=("Segoe UI", 10), fg=T["muted"], bg=T["bar"], cursor="hand2", padx=6)
        b.bind("<Enter>", lambda _e: b.config(fg=hover_fg))
        b.bind("<Leave>", lambda _e: b.config(fg=T["muted"]))
        return b

    close_btn = bar_button("✕", T["danger"])
    close_btn.pack(side="right")
    collapse_btn = bar_button("–", T["text"])
    collapse_btn.pack(side="right")

    # ---- body ------------------------------------------------------------
    body = tk.Frame(inner, bg=T["bg"])
    body.pack(fill="both", expand=True)
    title_label = tk.Label(body, text="", font=("Segoe UI", 17, "bold"), fg=T["text"], bg=T["bg"])
    detail_label = tk.Label(body, text="", font=("Segoe UI", 9), fg=T["muted"], bg=T["bg"])
    idle_label = tk.Label(body, text="● Ready · no focus session", font=("Segoe UI", 9), fg=T["muted"], bg=T["bg"])
    controls = tk.Frame(body, bg=T["bg"])
    button_style = dict(font=("Segoe UI", 8, "bold"), fg=T["text"], bg=T["button"], activebackground=T["button_hover"],
                        activeforeground=T["text"], relief="flat", bd=0, padx=8, pady=3,
                        disabledforeground="#5c6a85", cursor="hand2")

    def on_pause():
        post_command(state["pause_message"])
        poll_once()

    def on_lock():
        data, error = post_command(CARD_RETARGET_MESSAGE)
        lock_btn.config(text=flash_label(data, error), bg="#1e5f9e")
        root.after(FLASH_MS, lambda: lock_btn.config(text=LOCK_PILL_LABEL, bg=T["button"]))
        poll_once()

    def on_abort():
        post_command(CARD_ABORT_MESSAGE)
        poll_once()

    pause_btn = tk.Button(controls, text="⏸ PAUSE", command=on_pause, **button_style)
    lock_btn = tk.Button(controls, text=LOCK_PILL_LABEL, command=on_lock, **button_style)
    abort_btn = tk.Button(controls, text="■ ABORT", command=on_abort, **button_style)
    for btn in (pause_btn, lock_btn, abort_btn):
        btn.pack(side="left", padx=3)

    # ---- dragging (title bar), remembering where you left it ------------
    def start_drag(event):
        state["drag"] = (event.x_root - root.winfo_x(), event.y_root - root.winfo_y())

    def do_drag(event):
        dx, dy = state["drag"]
        root.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")

    def end_drag(_event):
        save_position(root.winfo_x(), root.winfo_y())

    for widget in (bar, grip, mini_label):
        widget.bind("<ButtonPress-1>", start_drag)
        widget.bind("<B1-Motion>", do_drag)
        widget.bind("<ButtonRelease-1>", end_drag)

    def apply_mode(mode):
        """Resize for the mode, keeping the card's bottom-right corner where
        it is (so growing from 'ready' to a session doesn't jump off-screen)."""
        if mode == state["mode"]:
            return
        old_w, old_h = SIZES.get(state["mode"] or "idle")
        new_w, new_h = SIZES[mode]
        right, bottom = root.winfo_x() + old_w, root.winfo_y() + old_h
        x, y = clamp_position(right - new_w, bottom - new_h, new_w, new_h, screen_w, screen_h)
        for wdg in (title_label, detail_label, idle_label, controls):
            wdg.pack_forget()
        if mode == "full":
            title_label.pack(pady=(6, 0))
            detail_label.pack()
            controls.pack(pady=(6, 6))
            body.pack(fill="both", expand=True)
        elif mode == "idle":
            idle_label.pack(pady=(6, 0))
            body.pack(fill="both", expand=True)
        else:
            body.pack_forget()
        grip.config(text="⋮⋮" if mode == "mini" else "⋮⋮  JARVIS")
        collapse_btn.config(text="▢" if mode == "mini" else "–")
        root.geometry(f"{new_w}x{new_h}+{x}+{y}")
        state["mode"] = mode

    def toggle_collapse():
        state["collapsed"] = not state["collapsed"]
        poll_once()

    collapse_btn.bind("<Button-1>", lambda _e: toggle_collapse())

    # 2.2.1: with no console windows any more, this ✕ is how you close
    # Jarvis. Closing the card makes jarvis_supervisor.py stop the server
    # too (ending a running focus session properly first). Asks first,
    # because a stray click would otherwise end a session mid-way.
    def on_close():
        from tkinter import messagebox
        if messagebox.askyesno("Close Jarvis?", CLOSE_CONFIRM_TEXT, parent=root):
            save_position(root.winfo_x(), root.winfo_y())
            root.destroy()

    close_btn.bind("<Button-1>", lambda _e: on_close())

    def poll_once():
        data, error = fetch_status()
        title, detail, color = overlay_text(data, error)
        state["color"] = color
        mode = card_mode(color, state["collapsed"])
        apply_mode(mode)
        title_label.config(text=title, fg=COLORS[color])
        detail_label.config(text=detail)
        idle_label.config(text=idle_line(detail, color), fg=COLORS[color] if color == "error" else T["muted"])
        mini_label.config(text=(title if color in ("on_track", "drifted") else ""), fg=COLORS[color])
        ctl = card_controls(data, error)
        state["pause_message"] = ctl["pause_message"]
        pause_btn.config(text=ctl["pause_label"])
        new_state = "normal" if ctl["enabled"] else "disabled"
        for btn in (pause_btn, lock_btn, abort_btn):
            btn.config(state=new_state)

    def poll():
        poll_once()
        root.after(POLL_MS, poll)

    poll()
    root.mainloop()


if __name__ == "__main__":
    run()
