"""
focus_session.py — Prompt 09: Focus Sessions (accountability timer + drift
detection), rebuilt to the actual pack spec after an earlier v1 shipped
short of it. See PROMPT_09_SPEC_NOTES below for exactly what changed and
why, so nothing here is a silent rewrite.

PROMPT_09_SPEC_NOTES:
  - Locking is now IDENTITY-based, not keyword-based. When a session
    starts, the frontmost app + (if it's a browser) the active tab's
    site are locked as the target. Drift = "you're no longer at that
    exact identity", not "your window title doesn't contain some words
    from your task description." This is what makes Prompt 10 (the
    deferred lock) and Prompt 11 (re-targeting) meaningful next steps —
    they both assume this identity-lock model, not keyword matching.
  - Privacy is now structural, not just a style choice in the message
    text. See _read_surface() — that is the ONLY place a raw app
    name or tab host ever exists as a plain string. Everything past it
    is a SHA-256 hash for comparison, or a boolean/counter. No app name,
    URL, or hash of either is ever included in an HTTP response.
  - Drift grace is 800ms (was wrongly 20s in the v1 build), the nag
    cadence is separately configurable by voice, and callouts escalate
    through three canned tiers instead of repeating one line forever.
  - Snooze, excuse, pause, resume, extend, and abort are all real voice
    commands now (see focus_commands.py), not just start/stop/status.
  - Session-end produces a report and updates a streak, backed by an
    aggregates-only ledger (ledger.json) whose keys are whitelisted —
    see LEDGER_ALLOWED_KEYS and test_focus_session.py's proof that nothing
    else can be written to it.
  - The Jarvis tab itself is exempt as "home base": being on it is
    never drift, and it never advances drift/on-track accounting either
    (it's neutral, not "good").

PROMPT_10_SPEC_NOTES ("The Deferred Lock"):
  - Fixes the specific bug this file's own comments used to call out as
    deliberately unsolved: starting a session from the Jarvis tab (or
    clicking FOCUS while a read happens to fail) used to lock the Jarvis
    tab itself as the target, which is a no-op target since home base
    never counts as drift either way.
  - start_session() now only locks immediately when the first read is
    already a usable, non-home-base identity. Otherwise the session
    starts in a `deferred` state with no target yet.
  - tick_settle() runs every watcher tick instead of record_tick() while
    deferred. It requires DEFERRED_SETTLE_TICKS (2) CONSECUTIVE ticks on
    the same non-home identity before trusting it enough to lock —
    exactly one reading proves nothing. Any tick back on the Jarvis tab,
    or an unreadable tick, resets that streak to zero. It never looks at
    anything but the same fresh, no-cache reader boundary record_tick()
    itself uses, so it can't guess from a background window or a second
    monitor — only from wherever the reader says the session actually is
    right now.
  - If the user simply never leaves the Jarvis tab, DEFERRED_FALLBACK_SECONDS
    (45s) forces an app-only lock instead of deferring forever.
  - "Locked on, sir." is spoken the moment either path locks. While
    deferred, the session's start line is DEFERRED_START_LINE instead of
    the normal confirmation, and asks what the user is focusing on;
    server.py's /focus/intent endpoint attaches the one-shot spoken (or
    typed) answer to the session via set_intent(), acknowledged with
    exactly INTENT_ACK_LINE ("Noted, sir.") — never parroted back.
  - `deferred` is exposed as a plain boolean on status_dict(), same
    privacy posture as everything else there: no identity data, just a
    flag the status line / viewer chip can react to.

PROMPT_11_SPEC_NOTES ("Lock This Tab"), 2.1.0:
  - retarget() is the ONE function every door goes through: voice ("lock on
    this tab" and friends, parsed in focus_commands.py) and the desktop
    card's LOCK THIS TAB pill (focus_overlay.py, via the same
    /focus/command route with from_card=true).
  - From a work tab: lock it now, silently forgive the current drift
    (refunded, no welcome-back, and any not-yet-spoken callout for it is
    dropped), say "Locked on, sir." From the Jarvis tab: re-arm the
    deferred lock, "Go to it, sir — I'll lock on where you land." From a
    non-browser app: lock the app (app-only). Nothing readable: say so and
    leave the lock alone.
  - THE CARD TRAP: a click on the card makes the card frontmost. When the
    request came from the card, or the frontmost process is Jarvis's own,
    read the browser's front tab straight from its debugging port instead.
  - Built on two reader fixes: _read_surface() now reads the frontmost APP
    first and only uses the tab when that app is a browser (switching to a
    non-browser app used to be invisible), and app-only locks now compare
    against each tick's APP hash (they used to read as instant drift).

PROMPT_12_SPEC_NOTES ("Name the Distraction"), 2.2.0:
  - The reader now also produces a spoken LABEL for the surface: a known
    distraction's name (DISTRACTION_NAMES), else the bare domain; for an
    app, its display name from windows_focus (the .exe's own File
    description). Home base never gets one.
  - THE PRIVACY LAW: the label is the one non-hash field on a Surface. The
    watcher hands it to record_tick() for that single tick; it can end up
    inside that tick's spoken line and nowhere else -- not on the session,
    not in status, the report, the ledger, or any note. Repeat counts
    ("Twice into YouTube") are kept by the anonymous identity hash.
    test_focus_session.py proves it with a made-up label.
  - Pools: four lines per tier, named and nameless; a repeat-visit pool; a
    first-callout pool naming both the intent and the distraction; drill-
    sergeant pools switched by voice. NAME_DISTRACTIONS turns naming off.

Everything in this file is plain Python with no OS dependency, so all of
it is exercised by a real test run in the dev sandbox before it reaches
server.py — see test_focus_session.py.
"""

import hashlib
import json
import os
import re
import time
import threading
import urllib.parse

# ---- named constants (every knob lives here, per the spec) -------------

TICK_SECONDS = 1                    # server-side accounting tick
DRIFT_GRACE_SECONDS = 0.8           # grace before a switch counts as drift
DEFAULT_NAG_INTERVAL_SECONDS = 30   # repeat-callout cadence while drifted, voice-settable
DEFAULT_SNOOZE_SECONDS = 15         # "give me fifteen seconds" default if no number given
STREAK_MIN_CLEAN_PERCENT = 85       # session must be >= this% on-target to grow the streak
MIN_NAG_INTERVAL_SECONDS = 5        # sanity floor so a voice command can't set nagging to 0/negative
MAX_NAG_INTERVAL_SECONDS = 600

# Prompt 10: "The Deferred Lock" -- how many consecutive ticks a non-home
# identity has to hold before it's trusted enough to lock onto, and how
# long to wait (while still stuck on the Jarvis tab / unreadable) before
# giving up on tab-level precision and locking onto the app only.
DEFERRED_SETTLE_TICKS = 2
DEFERRED_FALLBACK_SECONDS = 45

# Exact spoken lines the pack spec quotes verbatim -- kept as named
# constants (like the callout tiers above) so server.py and the tests
# both reference one source of truth instead of retyping the wording.
DEFERRED_START_LINE = "Go to what you're working on and I'll lock on there. And what are we focusing on?"
LOCKED_ON_LINE = "Locked on, sir."
INTENT_ACK_LINE = "Noted, sir."

# Prompt 11: "Lock This Tab" -- the re-target's spoken outcomes. The first
# two are the spec's own wording; the last three are the honest refusals
# for when there's nothing trustworthy to lock onto (a wrong lock is worse
# than no lock, per Prompt 10, so we never guess to avoid saying no).
RETARGET_REARM_LINE = "Go to it, sir — I'll lock on where you land."
RETARGET_BLIND_LINE = "I can't see where you are right now, sir, so I've left the lock where it was."
RETARGET_CARD_BLIND_LINE = (
    "I can't see your browser from the card, sir — go to the tab and say "
    "'lock on this tab' instead."
)
RETARGET_NO_SESSION_LINE = "There's no focus session running, sir — start one and I'll lock on."

# Prompt 10's "use the intent to colour the first callout and the report
# card" (missed in 2.0.0, added in 2.1.0). Only used when the intent is
# short enough to say back naturally -- the spec is explicit that a long
# dictated intent must never be parroted.
INTENT_SPOKEN_MAX_WORDS = 8
INTENT_FIRST_CALLOUT = [
    "Sir, this doesn't look like '{intent}' to me.",
    "'{intent}', I believe we said, sir.",
    "Wandering off from '{intent}' already, sir?",
    "Sir, '{intent}' is waiting for you.",
]

# The Jarvis tab is home base — never a drift, never counted either way.
# Matches whatever host server.py actually serves on (see server.py PORT).
JARVIS_HOSTS = {"localhost:4700", "127.0.0.1:4700", "localhost", "127.0.0.1"}

# 2.1.0: browser executables, mapped to the name tab_watcher reports for
# that browser's debugging port. When one of these is the frontmost app,
# the session looks at its active tab; when any other app is in front,
# the app itself is the identity. A browser in front that ISN'T the one
# reporting tabs (e.g. plain Chrome while Opera GX has tracking on) only
# gets an app-level identity -- we never borrow a background browser's
# tab, per Prompt 10's "only where I am can settle" law.
BROWSER_PROCESSES = {
    "opera.exe": "Opera GX",
    "chrome.exe": "Chrome",
    "msedge.exe": "Edge",
    "brave.exe": "Brave",
}

# Jarvis's own processes/windows: the desktop countdown card (a Python Tk
# window titled "Jarvis Focus") and the minimized consoles ("Jarvis -
# Server" etc). When one of these is frontmost the user is talking to
# Jarvis, so it's home base like the Jarvis tab -- never a drift, and
# never a lock target (Prompt 11's "card trap"). Caveat: any other Python
# GUI app you run would also read as home base.
OWN_PROCESS_NAMES = {"python.exe", "pythonw.exe", "py.exe"}
OWN_WINDOW_TITLE_PREFIXES = ("Jarvis - ", "Jarvis Focus")

LEDGER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "focus_ledger.json")

# The ONLY keys ever allowed into a ledger entry. Anything else raises —
# this is the structural half of the privacy rule, not just a convention.
LEDGER_ALLOWED_KEYS = {
    "timestamp", "planned_minutes", "active_minutes", "on_target_minutes",
    "drifts", "seconds_adrift", "percent", "completed",
}

# Three escalating canned pools. Deliberately generic — no app or site
# names belong here (that's Prompt 12's job later, with its own rule).
# ---- Prompt 12: Name the Distraction ------------------------------------
#
# THE SWITCH. False = every callout uses the nameless pools below, exactly
# as before Prompt 12 (nothing is named, even in the moment).
NAME_DISTRACTIONS = True

# Spoken names for the big distractions, matched on the site's host (and
# any subdomain of it). Everything else is read out as its bare domain
# ("Sir, example.com can wait."). Gmail is matched on mail.google.com
# only -- the rest of google.com isn't Gmail.
DISTRACTION_NAMES = {
    "instagram.com": "Instagram",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "x.com": "X",
    "twitter.com": "X",
    "reddit.com": "Reddit",
    "tiktok.com": "TikTok",
    "netflix.com": "Netflix",
    "mail.google.com": "Gmail",
}

# "Make it yours": your own tier-one lines for the site (or app) that
# actually gets you, keyed by its spoken name. When present, these are
# used instead of NAMED_TIER_1 for that distraction. Empty by default.
# Example: PERSONAL_LINES = {"YouTube": ["Sir, the algorithm has you again."]}
PERSONAL_LINES = {}

# Four lines per escalation tier (per the spec), so a long drift rotates
# through them instead of looping one phrase. Nameless = the fallback when
# nothing can be named (or naming is switched off).
CALLOUT_TIER_1 = [
    "Sir, you've drifted.",
    "You've stepped away from it, sir.",
    "Off target, sir.",
    "Sir, that isn't the task.",
]
CALLOUT_TIER_2 = [
    "Still drifted, sir — this is becoming a pattern.",
    "That's the second call, sir. Still away from it.",
    "You're still off task, sir.",
    "Still elsewhere, sir.",
]
CALLOUT_TIER_3 = [
    "Shall I note this session as unsuccessful, sir?",
    "Third call, sir. Would you like to abort or press on?",
    "This session isn't going well, sir, if I'm honest.",
    "Sir, the detours are becoming the project.",
]
CALLOUT_TIERS = [CALLOUT_TIER_1, CALLOUT_TIER_2, CALLOUT_TIER_3]

# Named pools: {label} is the distraction's spoken name. Tier 1 is the
# first callout of a drift; tiers 2 and 3 are the nags while that same
# drift goes on, so they're written as "still there", never "again".
NAMED_TIER_1 = [
    "Sir, {label} can wait.",
    "{label}, sir? I don't believe that's the task.",
    "A detour into {label}, sir. Shall we not?",
    "{label} will still be there later, sir.",
]
NAMED_TIER_2 = [
    "Still in {label}, sir. It is starting to look deliberate.",
    "Sir, {label} has had rather a lot of your attention.",
    "{label} is still open, sir. The work is getting lonely.",
    "Still {label}, sir. I did mention it.",
]
NAMED_TIER_3 = [
    "{label} is winning, sir. Abort, or press on?",
    "Sir, the detour into {label} is becoming the project.",
    "Still {label}, sir. Shall I note this session as unsuccessful?",
    "Sir, at this rate {label} deserves a line on the report card.",
]
NAMED_TIERS = [NAMED_TIER_1, NAMED_TIER_2, NAMED_TIER_3]

# First callout of a drift into somewhere you've already drifted into
# earlier this session. {count} is "Twice", "Three times", ... -- counted
# by the site's anonymous hash, never by its name.
NAMED_REPEAT = [
    "{count} into {label}, sir. It is starting to look deliberate.",
    "{count} in {label} now, sir. The detours are becoming the project.",
    "Back in {label}, sir. {count}, and I am keeping count.",
    "{label} again, sir. {count} this session.",
]

# The session's FIRST callout, when you told Jarvis what you're working on
# (Prompt 10's intent) and the distraction can be named.
NAMED_INTENT_FIRST = [
    "Sir — {label} does not look like '{intent}' to me.",
    "'{intent}', sir, not {label}.",
    "{label} and '{intent}' are not the same thing, sir.",
    "I don't recall '{intent}' involving {label}, sir.",
]

# Drill-sergeant pools, for the days you ask for them ("drill sergeant
# mode"; "go easy on me" turns it off). They name it too.
DRILL_NAMED_TIER_1 = [
    "{label}? Out. Now.",
    "Close {label}. Eyes front.",
    "Did I authorise {label}? I did not.",
    "{label} is not the mission. Move.",
]
DRILL_NAMED_TIER_2 = [
    "Still in {label}! Out, out, out!",
    "I can still see {label} from here. Move it!",
    "{label} is not getting this done. You are. Back to it!",
    "Why is {label} still open? Close it!",
]
DRILL_NAMED_TIER_3 = [
    "Enough {label}! You said you'd do this. Do it!",
    "{label} is winning and you are letting it. Back on target!",
    "Third call on {label}. Out of there, now!",
    "{label}, still? Unacceptable. Back to work!",
]
DRILL_NAMED_TIERS = [DRILL_NAMED_TIER_1, DRILL_NAMED_TIER_2, DRILL_NAMED_TIER_3]
DRILL_NAMED_REPEAT = [
    "{count} into {label}! What did I say? Out!",
    "{label} again! {count}! Out and stay out!",
    "Back in {label}. {count}. Move!",
    "{count} in {label}. Not today. Back to work!",
]
DRILL_NAMED_INTENT_FIRST = [
    "{label}? The job is '{intent}'. Out!",
    "'{intent}' — not {label}. Move it!",
    "Out of {label}. '{intent}'. Now.",
    "{label} is not '{intent}'. Back to it!",
]
DRILL_NAMELESS = [
    "Off target! Back to work!",
    "That is not the job. Move!",
    "Eyes front! Back on it!",
    "Out of there. Now!",
]

COUNT_WORDS = {2: "Twice", 3: "Three times", 4: "Four times", 5: "Five times", 6: "Six times"}

WELCOME_BACK_LINES = [
    "Welcome back, sir.",
    "Good, sir — back on track.",
    "There we are, sir.",
]


# ---- the privacy boundary: reader -> hash, nothing raw survives past here

def _host_from_url(url):
    try:
        parsed = urllib.parse.urlparse(url)
        return parsed.netloc.lower() or None
    except (ValueError, AttributeError):
        return None


def _hash_identity(app_name, tab_host):
    """
    Collapses (app, site) into one opaque hash. This is the one function
    in the whole system that ever sees a real app name or hostname
    alongside the comparison it's used for — the caller only ever gets
    the hash back, never the inputs. That's what "identities are
    compared and discarded inside the reader" means in practice: nothing
    downstream of this call can reconstruct what app or site it was.
    """
    raw = f"{(app_name or '').lower()}|{(tab_host or '').lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _is_home_base(tab_host):
    return bool(tab_host) and tab_host.lower() in JARVIS_HOSTS


class Surface:
    """
    What the reader saw this tick.
      kind: "jarvis" (the Jarvis tab), "own" (the desktop card or a Jarvis
            console), "tab" (a browser tab), "app" (a non-browser app, or a
            browser we can't read tabs from), "none" (nothing readable)
      identity_hash: the lock-comparable identity (tab-level for "tab",
            app-level for "app"), or None
      app_hash: the app-level hash for this read, used by app-only locks
      label: Prompt 12's spoken name ("Instagram", "example.com",
            "Discord"), or None. THE ONE non-hash field. It exists only for
            the tick it was read on: the watcher hands it to record_tick(),
            which may put it into that tick's spoken line and nowhere else.
            Nothing stores a Surface. Never set for home base.
    """
    __slots__ = ("kind", "identity_hash", "app_hash", "label")

    def __init__(self, kind, identity_hash=None, app_hash=None, label=None):
        self.kind = kind
        self.identity_hash = identity_hash
        self.app_hash = app_hash
        self.label = label

    @property
    def is_home_base(self):
        return self.kind in ("jarvis", "own")


def _label_for_host(host):
    """Prompt 12: the spoken name for a site. The big distractions by name
    (subdomains included), anything else as its bare domain -- no port, no
    leading www./m."""
    if not host:
        return None
    bare = host.lower().split(":", 1)[0]
    for prefix in ("www.", "m.", "mobile."):
        if bare.startswith(prefix):
            bare = bare[len(prefix):]
            break
    for domain, name in DISTRACTION_NAMES.items():
        if bare == domain or bare.endswith("." + domain):
            return name
    return bare or None


def _label_for_window(window):
    """Prompt 12: an app's display name, from the same reader that reports
    the frontmost app (windows_focus supplies "display_name" from the
    .exe's own File description -- what Task Manager shows). Falls back to
    the executable's name without ".exe"."""
    name = (window.get("display_name") or "").strip()
    if name:
        return name
    process = (window.get("process") or "").strip()
    if not process or process.lower() == "unknown":
        return None
    stem = process[:-4] if process.lower().endswith(".exe") else process
    return stem[:1].upper() + stem[1:] if stem else None


def _surface_from_tab(tab, browser_name, app_hash):
    host = _host_from_url(tab.get("url", ""))
    if _is_home_base(host):
        return Surface("jarvis", None, app_hash)
    return Surface("tab", _hash_identity(browser_name, host), app_hash, label=_label_for_host(host))


def _read_tab_surface(get_tab_fn):
    """Tab-only read, straight from the browser's debugging port. Used when
    there's no frontmost-window reader on this platform, and for Prompt
    11's card trap: the debugging port answers with the browser in the
    background, so it still sees the right tab after a click on the card
    has made the card itself the frontmost app."""
    tab = get_tab_fn() if get_tab_fn else None
    if not tab:
        return Surface("none")
    browser_name = tab.get("browser", "browser")
    return _surface_from_tab(tab, browser_name, _hash_identity(browser_name, None))


def _read_surface(get_tab_fn, get_window_fn):
    """
    THE reader. Calls the OS-level sources fresh every time (no caching —
    a stale cached read is exactly the failure mode the spec calls out)
    and reduces whatever it gets to a Surface of hashes. The raw app name,
    window title and URL go out of scope the moment this returns; nothing
    else in this module, and nothing in server.py, ever sees them.

    2.1.0: frontmost APP first, then the tab only if that app is a browser
    — per Prompt 09's spec ("lock the frontmost APP ... and when that app
    is Chrome-family, also lock the active TAB"). Before 2.1.0 this read
    the tab first whenever any tracked browser was running, so switching
    to a non-browser app was invisible (the tab behind it hadn't changed).
    """
    window = get_window_fn() if get_window_fn else None
    if window:
        process = (window.get("process") or "app").lower()
        title = window.get("title") or ""
        browser_name = BROWSER_PROCESSES.get(process)
        if browser_name is None:
            if process in OWN_PROCESS_NAMES or title.startswith(OWN_WINDOW_TITLE_PREFIXES):
                return Surface("own")
            app_hash = _hash_identity(process, None)
            return Surface("app", app_hash, app_hash, label=_label_for_window(window))

        app_hash = _hash_identity(process, None)
        tab = get_tab_fn() if get_tab_fn else None
        if tab and tab.get("browser") == browser_name:
            return _surface_from_tab(tab, browser_name, app_hash)
        # A browser is in front, but not one whose tabs we can read: all we
        # honestly know is the app.
        return Surface("app", app_hash, app_hash, label=_label_for_window(window))

    # No frontmost-window reader on this platform (or it failed this very
    # instant): fall back to tabs only, as before 2.1.0.
    return _read_tab_surface(get_tab_fn)


def _reader_boundary(get_tab_fn, get_window_fn):
    """Compatibility wrapper: (identity_hash, is_home_base) only, for
    callers that don't care which kind of surface it was."""
    surface = _read_surface(get_tab_fn, get_window_fn)
    if surface.is_home_base:
        return None, True
    return surface.identity_hash, False


# ---- the session state machine ------------------------------------------

class FocusSession:
    def __init__(self, task, minutes, target_hash=None, deferred=False, target_is_app_only=False,
                 target_app_hash=None):
        self.task = task.strip() if task else None
        self.planned_seconds = max(1, int(minutes * 60))
        self.started_at = time.time()
        self.ended_at = None
        self.ended_reason = None  # "completed" | "stopped" | "aborted" | None

        # Locked at start when the very first read is already a usable,
        # non-home-base identity. When it isn't (started from the Jarvis
        # tab itself, or the read failed at that instant), start_session()
        # passes deferred=True instead and target_hash stays None until
        # tick_settle() below locks it on for real -- see Prompt 10.
        self.target_hash = target_hash
        # 2.1.0: an app-only lock (a non-browser app, a browser we can't
        # read tabs from, or Prompt 10's 45s fallback) compares against the
        # APP hash of each tick, so any tab inside that app counts as on
        # target. Before this, an app-only target was compared against
        # tab-level identities and read as drift the moment you landed on
        # any tab.
        self.target_is_app_only = target_is_app_only
        # Prompt 16: the app half of the lock (a hash), so /focus/diag can say
        # 'on target' per lane. Diagnostic only; never leaves this process.
        self.target_app_hash = target_hash if target_is_app_only else target_app_hash
        self.deferred = deferred
        self.deferred_started_at = time.time() if deferred else None
        self._settle_candidate_hash = None
        self._settle_count = 0

        self.is_drifted = False
        self.drift_started_at = None
        self.last_nag_at = None
        self.escalation_tier = 0  # 0-indexed into CALLOUT_TIERS
        self.nag_interval_seconds = DEFAULT_NAG_INTERVAL_SECONDS
        self.drift_count = 0
        # Prompt 12: how many separate drifts this session went into each
        # distraction, keyed by its anonymous identity HASH (never its
        # name) -- enough to say "Twice into YouTube" when a name happens to
        # be available on that tick, without ever storing the name.
        self._drift_visits = {}
        self._current_drift_hash = None
        self._line_counter = 0  # rotates lines within a pool

        self.snoozed_until = None
        self.excused = False  # current excursion won't count once we return

        self.paused = False
        self.paused_at = None
        self.total_paused_seconds = 0.0

        self.total_drift_seconds = 0.0
        self.total_on_track_seconds = 0.0
        self._last_sample_at = self.started_at

        self._lock = threading.Lock()
        # (kind, text) pairs: kind is "callout" for drift callouts, "info"
        # for everything else. Only the text ever leaves via pop_announcements;
        # the kind lets a re-target drop callouts for a drift it just forgave.
        self._announcements = []

    # ---- time accounting -------------------------------------------------

    def _active_elapsed(self):
        """Elapsed time, excluding any time spent paused."""
        end = self.ended_at if self.ended_at else time.time()
        paused = self.total_paused_seconds
        if self.paused and self.paused_at:
            paused += (end - self.paused_at)
        return max(0.0, (end - self.started_at) - paused)

    def remaining_seconds(self):
        return max(0, int(self.planned_seconds - self._active_elapsed()))

    def is_time_up(self):
        return (not self.paused) and self.remaining_seconds() <= 0 and self.ended_at is None

    def extend(self, minutes):
        with self._lock:
            self.planned_seconds += max(0, int(minutes * 60))

    # NOTE (2.1.0): pause/resume/excuse no longer queue their own spoken
    # line. The command that triggers them already returns that line as
    # its reply, which the viewer speaks -- queueing it as well made every
    # one of them play twice once 2.0.2 stopped the desktop overlay
    # swallowing the duplicate. Announcements are now only for things
    # that happen on their own (drift, welcome-back, lock-on, session end)
    # or that were triggered from the desktop card, which has no voice of
    # its own (server.py queues those via announce()).

    def pause(self):
        with self._lock:
            if not self.paused and self.ended_at is None:
                self.paused = True
                self.paused_at = time.time()

    def resume(self):
        with self._lock:
            if self.paused:
                self.total_paused_seconds += time.time() - self.paused_at
                self.paused = False
                self.paused_at = None
                self._last_sample_at = time.time()

    def announce(self, text):
        """Queue a line for the viewer to speak on its next poll."""
        if text:
            with self._lock:
                self._announcements.append(("info", text))

    def snooze(self, seconds):
        with self._lock:
            self.snoozed_until = time.time() + max(1, seconds)

    def set_excuse(self):
        """'It's okay, I'm doing research' — refunds the current
        excursion (it won't count as drift time or a drift) and goes
        quiet until you're back on target."""
        with self._lock:
            if self.is_drifted:
                # Refund: this excursion doesn't count as a real drift.
                self.drift_count = max(0, self.drift_count - 1)
                self._refund_visit()
            self.excused = True
            self.drift_started_at = None

    def set_nag_interval(self, seconds):
        seconds = max(MIN_NAG_INTERVAL_SECONDS, min(MAX_NAG_INTERVAL_SECONDS, seconds))
        with self._lock:
            self.nag_interval_seconds = seconds

    # ---- drift logic -------------------------------------------------

    def record_tick(self, identity_hash, is_home_base, app_hash=None, label=None):
        """
        Called once per tick with hashes (or None if nothing was readable),
        a home-base boolean, and -- Prompt 12 -- this tick's spoken label.
        Updates drift state in place. app_hash is the app-level hash for
        this tick, compared instead of identity_hash when the lock is
        app-only (callers that don't pass it get the pre-2.1.0 behaviour).

        THE PRIVACY LAW: `label` is a local of this call. It may go into
        the line spoken on this tick and is never assigned to self or to
        anything that outlives the call. test_focus_session.py proves this
        with a made-up label.
        """
        with self._lock:
            now = time.time()
            gap = now - self._last_sample_at
            self._last_sample_at = now

            if self.paused:
                return  # accounting frozen while paused

            if is_home_base:
                return  # visiting Jarvis is neutral — never drift, never on-track

            if identity_hash is None:
                return  # nothing readable this tick — don't guess

            if self.target_is_app_only and app_hash is not None:
                compared = app_hash
            else:
                compared = identity_hash
            on_target = (self.target_hash is not None) and (compared == self.target_hash)

            if on_target:
                if self.is_drifted:
                    self.is_drifted = False
                    self.drift_started_at = None
                    self.escalation_tier = 0
                    self.excused = False
                    self._announcements.append((
                        "info", WELCOME_BACK_LINES[self.drift_count % len(WELCOME_BACK_LINES)]
                    ))
                self.total_on_track_seconds += gap
                return

            # off target
            if self.drift_started_at is None:
                self.drift_started_at = now
                return

            drift_elapsed = now - self.drift_started_at

            if not self.is_drifted:
                if drift_elapsed >= DRIFT_GRACE_SECONDS:
                    self.is_drifted = True
                    self.drift_count += 1
                    self.last_nag_at = now
                    self._current_drift_hash = identity_hash
                    visits = self._drift_visits.get(identity_hash, 0) + 1
                    self._drift_visits[identity_hash] = visits
                    # A repeat visit enters the escalation further up: the
                    # second drift into the same place nags from tier 2 on.
                    self.escalation_tier = min(visits, len(CALLOUT_TIERS)) - 1
                    if not self.excused and not self._is_snoozed(now):
                        self._announcements.append(("callout", self._drift_callout(label, visits)))
                self.total_drift_seconds += gap
                return

            # already drifted — accrue time, consider nagging again
            self.total_drift_seconds += gap
            if self.excused or self._is_snoozed(now):
                return
            if self.last_nag_at is not None and (now - self.last_nag_at) >= self.nag_interval_seconds:
                self.escalation_tier = min(self.escalation_tier + 1, len(CALLOUT_TIERS) - 1)
                self.last_nag_at = now
                self._announcements.append(("callout", self._nag_callout(label, self.escalation_tier)))

    def _is_snoozed(self, now):
        return self.snoozed_until is not None and now < self.snoozed_until

    # ---- Prompt 10: the deferred lock -------------------------------

    def tick_settle(self, get_tab_fn, get_window_fn):
        """
        Called once per tick INSTEAD of record_tick() while self.deferred
        is True. Never guesses: it only ever looks at what the reader
        boundary says is here right now (the exact same fresh, no-cache
        read record_tick() would use), so it can never lock onto a
        background window or a second monitor that merely happens to be
        running something -- only onto wherever the reader says the
        session actually is at the moment of the call.

        Settle rule: the first non-home-base identity that shows up on
        two CONSECUTIVE ticks becomes the lock target. A single tick
        proves nothing (you might just be passing through mid-click), so
        one solitary sighting resets the streak rather than counting.
        Any tick spent back on the Jarvis tab, or where nothing could be
        read at all, breaks the streak the same way.

        If none of that resolves within DEFERRED_FALLBACK_SECONDS (the
        user simply never leaves the Jarvis tab), give up on tab-level
        precision and lock onto whatever app is frontmost right then --
        an app-only lock is still better than deferring forever.
        """
        with self._lock:
            if not self.deferred or self.ended_at is not None or self.paused:
                return

            now = time.time()
            surface = _read_surface(get_tab_fn, get_window_fn)

            if surface.kind in ("tab", "app"):
                if surface.identity_hash == self._settle_candidate_hash:
                    self._settle_count += 1
                else:
                    self._settle_candidate_hash = surface.identity_hash
                    self._settle_count = 1

                if self._settle_count >= DEFERRED_SETTLE_TICKS:
                    self._lock_on(surface.identity_hash, app_only=(surface.kind == "app"), app_hash=surface.app_hash)
                    return
            else:
                # Still on the Jarvis tab or card, or nothing readable this
                # tick — either way that's not a sighting, so the streak
                # breaks.
                self._settle_candidate_hash = None
                self._settle_count = 0

            if now - self.deferred_started_at >= DEFERRED_FALLBACK_SECONDS:
                self._lock_on_app_only(surface)

    def _lock_on(self, identity_hash, app_only=False, app_hash=None):
        """Assumes the caller already holds self._lock."""
        self.target_hash = identity_hash
        self.target_app_hash = identity_hash if app_only else app_hash
        self.target_is_app_only = app_only
        self.deferred = False
        self.deferred_started_at = None
        self._settle_candidate_hash = None
        self._settle_count = 0
        self._last_sample_at = time.time()
        self._announcements.append(("info", LOCKED_ON_LINE))

    def _lock_on_app_only(self, surface):
        """Assumes the caller already holds self._lock. Fallback lock that
        uses only the app from this tick's read, never a tab -- so it stays
        true to 'app-only' even if that app is the browser still sitting on
        the Jarvis tab. If not even the app was readable, it locks onto a
        placeholder that nothing will ever match, which is the honest
        outcome: nothing is readable, so nothing can drift."""
        app_hash = surface.app_hash or _hash_identity("app", None)
        self._lock_on(app_hash, app_only=True)

    def set_intent(self, text):
        """The one-shot, no-wake-word answer to 'what are we focusing on?'
        while the lock is still deferred (or any time after — there's no
        reason to refuse a late answer). Sets the task label; the spoken
        acknowledgement is deliberately just 'Noted, sir.', never a
        parrot-back of what was said."""
        text = (text or "").strip()
        with self._lock:
            if text:
                self.task = text

    def _pick(self, pool, **fields):
        """Next line from a pool, rotating so a long drift or a run of
        drifts doesn't repeat itself. `fields` fill the placeholders."""
        line = pool[self._line_counter % len(pool)]
        self._line_counter += 1
        return line.format(**fields)

    def _drift_callout(self, label, visits):
        """The line for the moment a drift is declared.
          - The session's FIRST callout names your intent (Prompt 10) and,
            when it can, the distraction too (Prompt 12).
          - A repeat drift into the same place: "Twice into YouTube, sir."
          - Otherwise a tier-one line, named when there's a name.
        `label` is only ever read here and in _nag_callout, never kept."""
        label = label if NAME_DISTRACTIONS else None
        drill = _drill_mode
        intent = spoken_intent(self.task)
        if self.drift_count == 1 and intent:
            if label:
                return self._pick(DRILL_NAMED_INTENT_FIRST if drill else NAMED_INTENT_FIRST,
                                  label=label, intent=intent)
            if drill:
                return self._pick(DRILL_NAMELESS)
            return self._pick(INTENT_FIRST_CALLOUT, intent=intent)
        if label and visits >= 2:
            count = COUNT_WORDS.get(visits, f"{visits} times")
            return self._pick(DRILL_NAMED_REPEAT if drill else NAMED_REPEAT, label=label, count=count)
        if label and not drill and PERSONAL_LINES.get(label):
            return self._pick(PERSONAL_LINES[label])
        return self._tier_line(label, 0, drill)

    def _nag_callout(self, label, tier):
        """A repeat callout while the same drift goes on."""
        label = label if NAME_DISTRACTIONS else None
        return self._tier_line(label, tier, _drill_mode)

    def _tier_line(self, label, tier, drill):
        tier = min(tier, len(CALLOUT_TIERS) - 1)
        if label:
            pools = DRILL_NAMED_TIERS if drill else NAMED_TIERS
            return self._pick(pools[tier], label=label)
        if drill:
            return self._pick(DRILL_NAMELESS)
        return self._pick(CALLOUT_TIERS[tier])

    # ---- Prompt 11: re-target -------------------------------------------

    def _refund_visit(self):
        """Assumes the caller holds self._lock. A refunded drift doesn't
        count towards "Twice into ..." either."""
        h = self._current_drift_hash
        if h is not None and self._drift_visits.get(h, 0) > 0:
            self._drift_visits[h] -= 1
        self._current_drift_hash = None

    def _forgive_current_drift(self):
        """Assumes the caller holds self._lock. Moving the lock silently
        forgives whatever drift you were in: it's refunded like an excuse
        (doesn't count as a drift) and no welcome-back line is said."""
        if self.is_drifted:
            self.drift_count = max(0, self.drift_count - 1)
            self._refund_visit()
        # A callout for the forgiven drift that the viewer hasn't polled yet
        # (it polls every 4s) would otherwise play AFTER the re-target reply.
        self._announcements = [(k, t) for k, t in self._announcements if k != "callout"]
        self.is_drifted = False
        self.drift_started_at = None
        self.last_nag_at = None
        self.escalation_tier = 0
        self.excused = False

    def lock_now(self, identity_hash, app_only=False, app_hash=None):
        """Re-target to a surface we can see right now. Ends any deferral."""
        with self._lock:
            self._forgive_current_drift()
            self.target_hash = identity_hash
            self.target_app_hash = identity_hash if app_only else app_hash
            self.target_is_app_only = app_only
            self.deferred = False
            self.deferred_started_at = None
            self._settle_candidate_hash = None
            self._settle_count = 0
            self._last_sample_at = time.time()

    def rearm_deferred(self):
        """Re-target asked from the Jarvis tab: drop the current target and
        go back to Prompt 10's deferred lock, so the next surface you settle
        on becomes the target."""
        with self._lock:
            self._forgive_current_drift()
            self.target_hash = None
            self.target_is_app_only = False
            self.target_app_hash = None
            self.deferred = True
            self.deferred_started_at = time.time()
            self._settle_candidate_hash = None
            self._settle_count = 0
            self._last_sample_at = time.time()

    def pop_announcements(self):
        with self._lock:
            pending = self._announcements
            self._announcements = []
            # Prompt 13's relief valve: while quiet, drift callouts are
            # dropped (the drift itself is still counted); info lines stay.
            quiet = time.time() < _quiet_until
            return [text for kind, text in pending if not (quiet and kind == "callout")]

    # ---- Prompt 13: the eyes ----
    def phone_drift(self):
        """A phone pickup seen by the webcam counts like a tab drift (spec:
        "a phone drift is counted like a tab drift, with its own line
        pool"). Counted in drift_count only -- no new ledger field."""
        with self._lock:
            if self.ended_at is not None or self.paused:
                return None
            self.drift_count += 1
            self._line_counter += 1
            pool = DRILL_PHONE_LINES if _drill_mode else PHONE_LINES
            return pool[self._line_counter % len(pool)]

    # ---- lifecycle -------------------------------------------------

    def stop(self, reason="stopped"):
        with self._lock:
            if self.ended_at is None:
                self.ended_at = time.time()
                self.ended_reason = reason

    def report(self):
        """Aggregates-only summary — see LEDGER_ALLOWED_KEYS. This is
        what gets spoken AND what (optionally) gets written to the
        ledger; both draw from the same whitelisted numbers."""
        active_seconds = self._active_elapsed()
        active_minutes = active_seconds / 60
        on_target_minutes = self.total_on_track_seconds / 60
        percent = int(round((self.total_on_track_seconds / active_seconds) * 100)) if active_seconds > 0 else 0
        return {
            "timestamp": self.started_at,
            "planned_minutes": round(self.planned_seconds / 60, 2),
            "active_minutes": round(active_minutes, 2),
            "on_target_minutes": round(on_target_minutes, 2),
            "drifts": self.drift_count,
            "seconds_adrift": int(self.total_drift_seconds),
            "percent": percent,
            "completed": self.ended_reason == "completed",
        }

    def status_dict(self):
        with self._lock:
            return {
                "active": self.ended_at is None,
                "task": self.task,
                "remaining_seconds": self.remaining_seconds(),
                "planned_seconds": self.planned_seconds,
                "is_drifted": self.is_drifted,
                "deferred": self.deferred,
                "paused": self.paused,
                "excused": self.excused,
                "snoozed": self._is_snoozed(time.time()),
                "nag_interval_seconds": self.nag_interval_seconds,
                "drift_count": self.drift_count,
                "total_drift_seconds": int(self.total_drift_seconds),
                "total_on_track_seconds": int(self.total_on_track_seconds),
                "ended_reason": self.ended_reason,
            }
            # NOTE: deliberately no app/tab/hash field above — this dict
            # is exactly what crosses the HTTP boundary to the browser
            # and the desktop overlay. See test_focus_session.py's
            # test_privacy_* checks, which assert this by construction.


# ---- ledger (aggregates-only, whitelisted keys) -------------------------

def _load_ledger():
    if not os.path.exists(LEDGER_PATH):
        return []
    try:
        with open(LEDGER_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _write_ledger_entry(entry):
    """Validates the whitelist BEFORE writing — this is the enforcement
    point, not just documentation. Raises on any unexpected key so a
    future edit that adds a field accidentally can't smuggle identity
    data into the ledger without being caught immediately (see the
    test that asserts this raises)."""
    unexpected = set(entry.keys()) - LEDGER_ALLOWED_KEYS
    if unexpected:
        raise ValueError(f"Ledger entry has non-whitelisted keys: {unexpected}")

    entries = _load_ledger()
    entries.append(entry)
    with open(LEDGER_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)
    return entries


def current_streak():
    """Consecutive most-recent sessions (from the end of the ledger)
    that were completed and >= STREAK_MIN_CLEAN_PERCENT clean."""
    entries = _load_ledger()
    streak = 0
    for entry in reversed(entries):
        if entry.get("completed") and entry.get("percent", 0) >= STREAK_MIN_CLEAN_PERCENT:
            streak += 1
        else:
            break
    return streak


def spoken_intent(task):
    """The intent, if it's short enough to say back naturally; else None.
    The spec is explicit that a long dictated intent is never parroted."""
    if not task:
        return None
    task = task.strip()
    if not task or len(task.split()) > INTENT_SPOKEN_MAX_WORDS:
        return None
    return task


def report_line(report, ended_reason, task=None):
    """The spoken report card, coloured by the intent when there is a short
    one (Prompt 10). Lives here, not in server.py, so the watcher can speak
    it when a session runs out of time on its own (2.1.0: that used to end
    in silence)."""
    mins = int(report["on_target_minutes"])
    planned = report["planned_minutes"]
    intent = spoken_intent(task)
    lead = f"'{intent}': " if intent else ""
    if ended_reason == "aborted":
        return (
            f"Session aborted, sir — {lead}{mins} of {planned:g} planned minutes on target. "
            "No streak credit for this one."
        )
    opener = "Time's up, sir" if ended_reason == "completed" else "Focus session ended, sir"
    streak = current_streak()
    streak_line = f" Streak now at {streak}." if report.get("completed") else ""
    return (
        f"{opener} — {lead}{mins} of {planned:g} planned minutes on target, "
        f"{report['drifts']} drift{'s' if report['drifts'] != 1 else ''}, "
        f"{report['percent']}% clean.{streak_line}"
    )


# ---- module-level singleton + watcher thread ---------------------------

_current_session = None
_watcher_thread = None
# 2.1.0: one stop event PER watcher thread. With a single shared event,
# replacing a session set it and cleared it again microseconds later, so
# the old thread usually never noticed it was told to stop -- every
# restart left one more thread ticking the current session.
_watcher_stop_flag = threading.Event()

# Prompt 12: drill-sergeant callouts, switched on and off by voice ("drill
# sergeant mode" / "go easy on me"). Runtime only -- a server restart always
# comes back in the normal voice. Applies to the running session and any
# started later.
_drill_mode = False


# Prompt 13: lines for the webcam organ. Four each, like the other pools.
PHONE_LINES = [
    "The phone, sir. It'll keep.",
    "Eyes up, sir. The work is on the other screen.",
    "Whatever it is, sir, it was there before the session and will be there after.",
    "I see a phone, sir, and no work being done on it.",
]
DRILL_PHONE_LINES = [
    "Phone. Down. Now.",
    "That phone is not the mission. Put it away.",
    "Did I say you could check that? Back to work.",
    "Face-down on the desk. Move.",
]
SLOUCH_LINES = [
    "Posture, sir. You're folding like a deckchair.",
    "Sit up, sir. Your spine will thank you later.",
    "A little taller, sir, if you'd be so kind.",
    "You've sunk a few inches, sir.",
]
AWAY_LINES = [
    "I'll hold the fort, sir.",
    "Stepped away, sir? I'll be here.",
    "The chair is empty, sir. I'll keep count.",
    "Gone walkabout, sir. Noted.",
]
QUIET_SECONDS = 180
_quiet_until = 0.0


def set_quiet(seconds=QUIET_SECONDS):
    """"Give me a minute": every nudge goes quiet for three minutes."""
    global _quiet_until
    _quiet_until = time.time() + seconds
    return _quiet_until


def is_quiet():
    return time.time() < _quiet_until


def set_drill_mode(on):
    global _drill_mode
    _drill_mode = bool(on)


def drill_mode():
    return _drill_mode


def _watcher_loop(session, stop_flag, get_tab_fn, get_window_fn):
    while not stop_flag.is_set():
        if session.ended_at is not None:
            break
        if session.is_time_up():
            session.stop(reason="completed")
            _finalize_ledger(session)
            # The spoken report card when time simply runs out (2.1.0 --
            # this used to end in silence; only "stop" produced a report).
            session.announce(report_line(session.report(), "completed", session.task))
            break
        if session.deferred:
            # Prompt 10: the lock hasn't settled yet -- watch for the user
            # to leave the Jarvis tab and settle somewhere, or fall back to
            # an app-only lock after long enough. Normal drift accounting
            # doesn't start until this resolves.
            session.tick_settle(get_tab_fn, get_window_fn)
        else:
            surface = _read_surface(get_tab_fn, get_window_fn)
            # Prompt 12: surface.label rides this ONE tick into the spoken
            # line (if a callout happens) and is gone when `surface` is
            # replaced on the next tick.
            session.record_tick(surface.identity_hash, surface.is_home_base,
                                app_hash=surface.app_hash, label=surface.label)
        stop_flag.wait(TICK_SECONDS)


def _finalize_ledger(session):
    try:
        _write_ledger_entry(session.report())
    except Exception:
        pass  # a ledger write failure should never crash the watcher thread


def start_session(task, minutes, get_tab_fn=None, get_window_fn=None):
    """
    Prompt 10 (The Deferred Lock): the naive version of this locked
    immediately on whatever the reader saw at start(), which meant a
    session started from the Jarvis tab itself just locked the Jarvis
    tab as its own target -- useless, since home base is defined as
    never drift. Now: if the very first read is already a usable,
    non-home-base identity, lock immediately as before (no reason to
    make the user wait when we already know where they are). Otherwise
    -- started from the Jarvis tab, or the read failed at this exact
    click -- defer, and let tick_settle() (driven by the watcher loop)
    do the locking once the user actually settles somewhere.
    """
    global _current_session, _watcher_thread, _watcher_stop_flag
    stop_session(reason="replaced")

    surface = _read_surface(get_tab_fn, get_window_fn)
    if surface.kind in ("tab", "app"):
        session = FocusSession(
            task, minutes, target_hash=surface.identity_hash, deferred=False,
            target_is_app_only=(surface.kind == "app"), target_app_hash=surface.app_hash,
        )
    else:
        session = FocusSession(task, minutes, target_hash=None, deferred=True)
    _current_session = session

    _watcher_stop_flag = threading.Event()
    _watcher_thread = threading.Thread(
        target=_watcher_loop,
        args=(session, _watcher_stop_flag, get_tab_fn, get_window_fn),
        daemon=True,
        name="focus-watcher",
    )
    _watcher_thread.start()
    return session


def stop_session(reason="stopped"):
    if _current_session and _current_session.ended_at is None:
        _current_session.stop(reason=reason)
        _finalize_ledger(_current_session)
    _watcher_stop_flag.set()


def announce(text):
    """Queue a line for the viewer to speak (used for actions taken from
    the desktop card, which has no voice of its own)."""
    if _current_session is not None:
        _current_session.announce(text)


def retarget(get_tab_fn=None, get_window_fn=None, from_card=False):
    """
    Prompt 11: move the lock without aborting the session. The ONE function
    every door goes through -- voice ("lock on this tab") and the desktop
    card's LOCK THIS TAB pill alike. Returns (outcome, spoken_line), where
    outcome is one of "locked", "rearmed", "blind", "no_session".

      - From a work tab or app: lock it now, forgive the current drift.
      - From the Jarvis tab: re-arm the deferred lock.
      - THE CARD TRAP: when the request came from the card, or the frontmost
        process is Jarvis's own (the card, a console), "frontmost" is us,
        not the user's work -- so read the browser's front tab directly over
        its debugging port instead, which answers even with the browser in
        the background.
      - Nothing trustworthy to lock onto: say so and leave the lock alone.
    """
    session = _current_session
    if session is None or session.ended_at is not None:
        return "no_session", RETARGET_NO_SESSION_LINE

    surface = _read_surface(get_tab_fn, get_window_fn)
    card_trap = from_card or surface.kind == "own"
    if card_trap:
        surface = _read_tab_surface(get_tab_fn)

    if surface.kind == "jarvis":
        session.rearm_deferred()
        return "rearmed", RETARGET_REARM_LINE
    if surface.kind in ("tab", "app"):
        session.lock_now(surface.identity_hash, app_only=(surface.kind == "app"), app_hash=surface.app_hash)
        return "locked", LOCKED_ON_LINE
    return "blind", (RETARGET_CARD_BLIND_LINE if card_trap else RETARGET_BLIND_LINE)


def diag(get_tab_fn=None, get_window_fn=None):
    """
    Prompt 16's field proof: what the RUNNING server sees, right now, as
    booleans, counts and statuses ONLY. No app names, titles, URLs, labels
    or hashes -- the hashes are compared here and only the yes/no leaves.
    Does one fresh read, exactly like a watcher tick (never cached).
    """
    try:
        window = get_window_fn() if get_window_fn else None
    except Exception:
        window = None
    process = ((window or {}).get("process") or "").lower()
    is_browser = process in BROWSER_PROCESSES
    try:
        surface = _read_surface(get_tab_fn, get_window_fn)
    except Exception:
        surface = Surface("none")
    if window is None:
        tab_status = "no_window_reader" if surface.kind == "none" else "tab_only"
    elif not is_browser:
        tab_status = "not_a_browser"
    elif surface.kind in ("tab", "jarvis"):
        tab_status = "read"
    else:
        tab_status = "unreadable"   # a browser whose tabs we can't read (debug port off?)
    s = _current_session
    active = s is not None and s.ended_at is None
    out = {
        "frontmost_readable": window is not None or surface.kind != "none",
        "frontmost_is_browser": is_browser,
        "tab_read": tab_status,
        "front_is_jarvis": surface.kind == "jarvis",
        "front_is_own_card": surface.kind == "own",
        "hash_present": surface.identity_hash is not None,
        "session_on": active,
        "tick_thread_alive": bool(_watcher_thread is not None and _watcher_thread.is_alive()),
        "quiet": is_quiet(),
        "drill": _drill_mode,
    }
    if s is not None:
        with s._lock:
            app_only = s.target_is_app_only
            out.update({
                "deferred": s.deferred,
                "settle_ticks": s._settle_count,
                "settle_needed": DEFERRED_SETTLE_TICKS,
                "has_app_target": (s.target_app_hash is not None),
                "has_tab_target": (s.target_hash is not None and not app_only),
                "lock_is_app_only": app_only,
                "on_target_app": (s.target_app_hash is not None and surface.app_hash == s.target_app_hash),
                "on_target_tab": (not app_only and s.target_hash is not None
                                  and surface.kind == "tab" and surface.identity_hash == s.target_hash),
                "is_drifted": s.is_drifted,
                "excused": s.excused,
                "paused": s.paused,
                "drift_count": s.drift_count,
            })
    else:
        out.update({"deferred": False, "settle_ticks": 0, "settle_needed": DEFERRED_SETTLE_TICKS,
                    "has_app_target": False, "has_tab_target": False, "lock_is_app_only": False,
                    "on_target_app": False, "on_target_tab": False, "is_drifted": False,
                    "excused": False, "paused": False, "drift_count": 0})
    return out


def get_current_session():
    return _current_session


def get_status():
    if _current_session is None:
        return {"active": False, "task": None}
    return _current_session.status_dict()


def pop_announcements():
    if _current_session is None:
        return []
    return _current_session.pop_announcements()
