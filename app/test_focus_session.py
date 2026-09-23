"""
Real-execution test for focus_session.py, rebuilt for the identity-hash
based Prompt 09 spec. Run directly: python3 test_focus_session.py
"""

import json
import threading
import os
import time
import focus_session as fs

# ---- protect the real focus ledger (2.2.0) ---------------------------------
# These tests start, stop and wipe sessions against the same focus_ledger.json
# that holds the user's real history and streak. Save its exact bytes now and
# put them back when the test run ends, however it ends.
import atexit as _atexit
import os as _os
import focus_session as _fs_for_ledger
_LEDGER_SNAPSHOT = (open(_fs_for_ledger.LEDGER_PATH, "rb").read()
                    if _os.path.exists(_fs_for_ledger.LEDGER_PATH) else None)
def _restore_real_ledger():
    if _LEDGER_SNAPSHOT is None:
        if _os.path.exists(_fs_for_ledger.LEDGER_PATH):
            _os.remove(_fs_for_ledger.LEDGER_PATH)
    else:
        with open(_fs_for_ledger.LEDGER_PATH, "wb") as _f:
            _f.write(_LEDGER_SNAPSHOT)
_atexit.register(_restore_real_ledger)
# ---------------------------------------------------------------------------

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


def fresh_ledger():
    if os.path.exists(fs.LEDGER_PATH):
        os.remove(fs.LEDGER_PATH)


def advance(session, seconds):
    """Simulate elapsed time consistently across every timestamp the
    session holds, so record_tick() sees genuinely-consistent wall-clock
    time the same way it would in real operation."""
    session.started_at -= seconds
    session._last_sample_at -= seconds
    if session.drift_started_at is not None:
        session.drift_started_at -= seconds
    if session.last_nag_at is not None:
        session.last_nag_at -= seconds
    if session.paused_at is not None:
        session.paused_at -= seconds


TARGET = fs._hash_identity("Chrome", "docs.example.com")
OTHER = fs._hash_identity("Chrome", "youtube.com")

print("Test 1: identity hashing is deterministic and irreversible-looking")
h1 = fs._hash_identity("Chrome", "docs.example.com")
h2 = fs._hash_identity("Chrome", "docs.example.com")
h3 = fs._hash_identity("Chrome", "youtube.com")
check("same identity hashes the same", h1 == h2)
check("different identity hashes differently", h1 != h3)
check("hash does not contain the plaintext", "docs.example.com" not in h1 and "chrome" not in h1.lower())

print("\nTest 2: reader boundary reduces tab data to (hash, is_home_base) only")
tab = {"browser": "Chrome", "title": "My Doc - Google Docs", "url": "https://docs.example.com/abc"}
result_hash, is_home = fs._reader_boundary(lambda: tab, None)
check("reader returns a hash, not raw data", isinstance(result_hash, str) and len(result_hash) == 64)
check("not flagged as home base", is_home is False)

jarvis_tab = {"browser": "Chrome", "title": "Jarvis", "url": "http://localhost:4700/"}
result_hash2, is_home2 = fs._reader_boundary(lambda: jarvis_tab, None)
check("Jarvis's own tab is recognized as home base", is_home2 is True)
check("home base returns no identity hash", result_hash2 is None)

print("\nTest 3: on-target activity never drifts")
session = fs.FocusSession("write the doc", 25, target_hash=TARGET)
session.record_tick(TARGET, False)
check("not drifted on first on-target tick", not session.is_drifted)
advance(session, 30)
session.record_tick(TARGET, False)
check("still not drifted after 30s on-target", not session.is_drifted)
check("on-track seconds accumulated", session.total_on_track_seconds >= 29)

print("\nTest 4: off-target within the 800ms grace does not drift")
session2 = fs.FocusSession("write the doc", 25, target_hash=TARGET)
session2.record_tick(TARGET, False)
session2.record_tick(OTHER, False)  # first off-target tick just opens the grace window
check("not drifted immediately (grace window just opened)", not session2.is_drifted)

print("\nTest 5: sustained off-target past the grace period DOES trigger drift + tier-1 callout")
session3 = fs.FocusSession("write the doc", 25, target_hash=TARGET)
session3.record_tick(TARGET, False)
session3.record_tick(OTHER, False)          # opens grace window
advance(session3, 2)                         # past 800ms grace
session3.record_tick(OTHER, False)
check("now marked drifted", session3.is_drifted)
check("drift_count incremented", session3.drift_count == 1)
msgs = session3.pop_announcements()
check("exactly one callout produced", len(msgs) == 1)
# 2.1.0 (Prompt 10's "colour the first callout" with the intent): this
# session has a short task, so its FIRST callout names it.
expected_intent_lines = [l.format(intent="write the doc") for l in fs.INTENT_FIRST_CALLOUT]
check("first callout is coloured by the intent ('write the doc')", msgs[0] in expected_intent_lines)

print("\nTest 5b: with no intent, the first callout is a plain tier-1 line")
session3b = fs.FocusSession(None, 25, target_hash=TARGET)
session3b.record_tick(TARGET, False)
session3b.record_tick(OTHER, False)
advance(session3b, 2)
session3b.record_tick(OTHER, False)
msgs_b = session3b.pop_announcements()
check("plain tier-1 callout, no app/site name in it", len(msgs_b) == 1 and msgs_b[0] in fs.CALLOUT_TIER_1)

print("\nTest 5c: a long dictated intent is never parroted in the callout")
long_task = "finish the quarterly planning document and then also email the whole team about it"
session3c = fs.FocusSession(long_task, 25, target_hash=TARGET)
session3c.record_tick(TARGET, False)
session3c.record_tick(OTHER, False)
advance(session3c, 2)
session3c.record_tick(OTHER, False)
msgs_c = session3c.pop_announcements()
check("long intent falls back to a plain tier-1 line", len(msgs_c) == 1 and msgs_c[0] in fs.CALLOUT_TIER_1)
check("no part of the long intent is spoken", "quarterly" not in msgs_c[0])

print("\nTest 6: nag cadence escalates through tiers while drift persists")
advance(session3, fs.DEFAULT_NAG_INTERVAL_SECONDS + 1)
session3.record_tick(OTHER, False)
msgs2 = session3.pop_announcements()
check("second nag fired after the cadence interval", len(msgs2) == 1)
check("second nag is tier-2", msgs2[0] in fs.CALLOUT_TIER_2)
advance(session3, fs.DEFAULT_NAG_INTERVAL_SECONDS + 1)
session3.record_tick(OTHER, False)
msgs3 = session3.pop_announcements()
check("third nag is tier-3", msgs3[0] in fs.CALLOUT_TIER_3)
advance(session3, fs.DEFAULT_NAG_INTERVAL_SECONDS + 1)
session3.record_tick(OTHER, False)
msgs4 = session3.pop_announcements()
check("stays at tier-3 (caps, does not go out of range)", msgs4[0] in fs.CALLOUT_TIER_3)

print("\nTest 7: returning to target clears drift and says welcome back")
transitioned_msgs = session3.pop_announcements()  # drain anything pending
session3.record_tick(TARGET, False)
back_msgs = session3.pop_announcements()
check("no longer drifted", not session3.is_drifted)
check("welcome-back message produced", any(m in fs.WELCOME_BACK_LINES for m in back_msgs))

print("\nTest 8: home base (Jarvis tab) is neutral, never drift, never on-track")
session4 = fs.FocusSession("write the doc", 25, target_hash=TARGET)
session4.record_tick(TARGET, False)
advance(session4, 5)
session4.record_tick(None, True)  # visiting the Jarvis tab
check("home base tick produces no drift", not session4.is_drifted)
check("home base tick adds no on-track time", session4.total_on_track_seconds < 1)
check("home base tick adds no drift time", session4.total_drift_seconds == 0)

print("\nTest 9: snooze suppresses callouts for its duration")
session5 = fs.FocusSession("write the doc", 25, target_hash=TARGET)
session5.record_tick(TARGET, False)
session5.snooze(10)
session5.record_tick(OTHER, False)
advance(session5, 2)
session5.record_tick(OTHER, False)
check("drifted internally", session5.is_drifted)
check("no callout while snoozed", session5.pop_announcements() == [])

print("\nTest 10: excuse refunds the current excursion and stays quiet")
session6 = fs.FocusSession("write the doc", 25, target_hash=TARGET)
session6.record_tick(TARGET, False)
session6.record_tick(OTHER, False)
advance(session6, 2)
session6.record_tick(OTHER, False)  # now drifted, drift_count=1
check("drift_count is 1 before excuse", session6.drift_count == 1)
session6.pop_announcements()
session6.set_excuse()
check("drift_count refunded to 0", session6.drift_count == 0)
# 2.1.0: the excuse's "Noted, sir. I'll stay quiet..." is the command's
# REPLY (spoken by the viewer directly). Queueing it as an announcement too
# made it play twice.
check("excuse queues nothing (its line is the command's reply, not an announcement)",
      session6.pop_announcements() == [])
advance(session6, fs.DEFAULT_NAG_INTERVAL_SECONDS + 1)
session6.record_tick(OTHER, False)
check("still no nag while excused", session6.pop_announcements() == [])

print("\nTest 11: pause freezes the clock and drift accounting")
session7 = fs.FocusSession("write the doc", 5, target_hash=TARGET)
before_remaining = session7.remaining_seconds()
session7.pause()
time.sleep(1.2)
session7.record_tick(OTHER, False)
check("paused session does not drift", not session7.is_drifted)
check("remaining time did not advance meaningfully while paused",
      abs(session7.remaining_seconds() - before_remaining) <= 1)
session7.resume()
check("resume clears paused flag", not session7.paused)

print("\nTest 12: extend adds planned time")
session8 = fs.FocusSession("write the doc", 5, target_hash=TARGET)
before = session8.planned_seconds
session8.extend(10)
check("planned_seconds increased by 10 minutes", session8.planned_seconds == before + 600)

print("\nTest 13: nag interval is voice-settable and clamped to sane bounds")
session9 = fs.FocusSession("write the doc", 5, target_hash=TARGET)
session9.set_nag_interval(45)
check("nag interval set correctly", session9.nag_interval_seconds == 45)
session9.set_nag_interval(0)
check("nag interval clamped to floor", session9.nag_interval_seconds == fs.MIN_NAG_INTERVAL_SECONDS)
session9.set_nag_interval(99999)
check("nag interval clamped to ceiling", session9.nag_interval_seconds == fs.MAX_NAG_INTERVAL_SECONDS)

print("\nTest 14: status_dict is PRIVACY-SAFE by construction (no identity fields)")
session10 = fs.FocusSession("write the doc", 25, target_hash=TARGET)
status = session10.status_dict()
serialized = json.dumps(status)
check("no 'hash' key in status", "hash" not in status)
check("no 'app' or 'tab' or 'url' or 'host' key in status",
      not any(k for k in status if k.lower() in ("app", "tab", "url", "host")))
check("the actual target hash value never appears in the serialized status", TARGET not in serialized)
check("status is a whitelist of booleans/counters/strings the user typed themselves",
      set(status.keys()) == {
          "active", "task", "remaining_seconds", "planned_seconds", "is_drifted",
          "deferred", "paused", "excused", "snoozed", "nag_interval_seconds", "drift_count",
          "total_drift_seconds", "total_on_track_seconds", "ended_reason",
      })

print("\nTest 15: ledger enforces its key whitelist structurally, not just by convention")
fresh_ledger()
good_entry = {
    "timestamp": time.time(), "planned_minutes": 25, "active_minutes": 25,
    "on_target_minutes": 23, "drifts": 1, "seconds_adrift": 40,
    "percent": 92, "completed": True,
}
fs._write_ledger_entry(good_entry)
check("valid ledger entry writes without error", os.path.exists(fs.LEDGER_PATH))

bad_entry = dict(good_entry)
bad_entry["app_name"] = "Chrome"  # an identity field sneaking in
threw = False
try:
    fs._write_ledger_entry(bad_entry)
except ValueError:
    threw = True
check("ledger write REJECTS a non-whitelisted key (e.g. app_name)", threw)

with open(fs.LEDGER_PATH) as f:
    ledger_contents = f.read()
check("the rejected entry's extra key never made it to disk", "app_name" not in ledger_contents)

print("\nTest 16: streak grows only on completed, >=85%-clean sessions, breaks on a bad one")
fresh_ledger()
fs._write_ledger_entry({"timestamp": 1, "planned_minutes": 25, "active_minutes": 25, "on_target_minutes": 24, "drifts": 0, "seconds_adrift": 0, "percent": 96, "completed": True})
fs._write_ledger_entry({"timestamp": 2, "planned_minutes": 25, "active_minutes": 25, "on_target_minutes": 23, "drifts": 1, "seconds_adrift": 60, "percent": 92, "completed": True})
check("streak is 2 after two clean sessions", fs.current_streak() == 2)
fs._write_ledger_entry({"timestamp": 3, "planned_minutes": 25, "active_minutes": 25, "on_target_minutes": 10, "drifts": 5, "seconds_adrift": 900, "percent": 40, "completed": True})
check("streak resets to 0 after a session below 85%", fs.current_streak() == 0)
fresh_ledger()

print("\nTest 17: report() produces exactly the ledger-whitelisted keys")
session11 = fs.FocusSession("write the doc", 25, target_hash=TARGET)
session11.record_tick(TARGET, False)
advance(session11, 60)
session11.stop(reason="completed")
report = session11.report()
check("report keys are exactly the ledger whitelist", set(report.keys()) == fs.LEDGER_ALLOWED_KEYS)
fs._write_ledger_entry(report)  # should not raise
check("a real session's report writes to the ledger without error", True)
fresh_ledger()

print("\nTest 18: module-level session lifecycle (start/get/stop) with a real reader function")
fs.stop_session()
check("no active session initially", fs.get_status()["active"] is False)
fake_tab = {"browser": "Chrome", "title": "x", "url": "https://example.com/"}
session12 = fs.start_session("deep work block", 5, get_tab_fn=lambda: fake_tab, get_window_fn=None)
check("session started and is active", fs.get_status()["active"] is True)
check("task text carried through", fs.get_status()["task"] == "deep work block")
check("target_hash was captured from the reader at start", session12.target_hash is not None)
fs.stop_session(reason="stopped")
time.sleep(0.1)
check("session inactive after stop", fs.get_status()["active"] is False)
fresh_ledger()

print("\nTest 19: deferred lock requires two CONSECUTIVE sightings of the same identity")
sessionD = fs.FocusSession("write the doc", 25, target_hash=None, deferred=True)
check("starts deferred with no target", sessionD.deferred and sessionD.target_hash is None)
tabA = {"browser": "Chrome", "title": "x", "url": "https://docs.example.com/"}
sessionD.tick_settle(lambda: tabA, None)
check("one sighting alone does not lock", sessionD.deferred and sessionD.target_hash is None)
tabB = {"browser": "Chrome", "title": "y", "url": "https://youtube.com/"}
sessionD.tick_settle(lambda: tabB, None)  # a DIFFERENT identity -- streak restarts, not "two in a row"
check("a different identity on tick 2 does not lock either", sessionD.deferred and sessionD.target_hash is None)
sessionD.tick_settle(lambda: tabB, None)  # same identity as the previous tick now
check("the same identity twice in a row locks", not sessionD.deferred and sessionD.target_hash is not None)
locked_msgs = sessionD.pop_announcements()
check("locking announces exactly 'Locked on, sir.'", locked_msgs == [fs.LOCKED_ON_LINE])

print("\nTest 20: a Jarvis-tab or unreadable tick breaks the settle streak")
sessionE = fs.FocusSession("write the doc", 25, target_hash=None, deferred=True)
tabC = {"browser": "Chrome", "title": "z", "url": "https://example.org/"}
sessionE.tick_settle(lambda: tabC, None)              # sighting 1
sessionE.tick_settle(lambda: jarvis_tab, None)        # back on Jarvis tab -- breaks the streak
sessionE.tick_settle(lambda: tabC, None)              # sighting 1 again (streak restarted)
check("still deferred -- the Jarvis-tab tick in between broke the streak", sessionE.deferred)
sessionE.tick_settle(lambda: tabC, None)              # sighting 2 in a row now
check("locks after two REAL consecutive sightings", not sessionE.deferred)

print("\nTest 21: never leaving the Jarvis tab falls back to an app-only lock after 45s")
window_only = {"process": "chrome.exe", "title": "Jarvis", "pid": 123}
sessionF = fs.FocusSession("write the doc", 25, target_hash=None, deferred=True)
sessionF.deferred_started_at -= (fs.DEFERRED_FALLBACK_SECONDS + 1)  # simulate elapsed time
sessionF.tick_settle(lambda: jarvis_tab, lambda: window_only)  # still on the Jarvis tab every tick
check("falls back to an app-only lock rather than waiting forever", not sessionF.deferred)
check("target_hash came from the frontmost app, not the Jarvis tab", sessionF.target_hash is not None)
check("app-only hash matches the window's process, ignoring tab host",
      sessionF.target_hash == fs._hash_identity("chrome.exe", None))
check("fallback also announces 'Locked on, sir.'", fs.LOCKED_ON_LINE in sessionF.pop_announcements())

print("\nTest 22: normal drift accounting stays inert while a lock is still deferred")
sessionG = fs.FocusSession("write the doc", 25, target_hash=None, deferred=True)
sessionG.tick_settle(lambda: {"browser": "Chrome", "title": "x", "url": "https://example.com/"}, None)
check("total_on_track_seconds untouched while still settling", sessionG.total_on_track_seconds == 0)
check("total_drift_seconds untouched while still settling", sessionG.total_drift_seconds == 0)
check("is_drifted stays False while still settling", not sessionG.is_drifted)

print("\nTest 23: set_intent attaches the spoken/typed answer as the session's task")
sessionH = fs.FocusSession(None, 25, target_hash=TARGET)
check("no task yet", sessionH.task is None)
sessionH.set_intent("the Acme proposal")
check("task set from the intent answer", sessionH.task == "the Acme proposal")
sessionH.set_intent("   ")
check("a blank/whitespace-only answer doesn't wipe out a real task", sessionH.task == "the Acme proposal")

print("\nTest 24: start_session() defers for real when nothing is readable at all")
fs.stop_session()
session13 = fs.start_session("deep work block", 5, get_tab_fn=None, get_window_fn=None)
check("session starts deferred when nothing is readable at all", fs.get_status()["deferred"] is True)
check("no target locked yet", session13.target_hash is None)
fs.stop_session(reason="stopped")
time.sleep(0.1)
fresh_ledger()

print("\nTest 25: start_session() defers when started from the Jarvis tab itself")
session14 = fs.start_session("deep work block", 5, get_tab_fn=lambda: jarvis_tab, get_window_fn=None)
check("session starts deferred from the Jarvis tab", fs.get_status()["deferred"] is True)
check("status exposes 'deferred' as a plain boolean, no identity data alongside it",
      isinstance(fs.get_status()["deferred"], bool))
fs.stop_session(reason="stopped")
time.sleep(0.1)
fresh_ledger()

print("\nTest 26: start_session() still locks immediately when the first read is already usable")
session15 = fs.start_session("deep work block", 5, get_tab_fn=lambda: tabA, get_window_fn=None)
check("not deferred -- first read was already a real, non-home identity", fs.get_status()["deferred"] is False)
check("target_hash captured immediately, same as before Prompt 10", session15.target_hash is not None)
fs.stop_session(reason="stopped")
time.sleep(0.1)
fresh_ledger()

# ===========================================================================
# 2.1.0 — Prompt 11 (Lock This Tab) and the reader fixes it depends on
# ===========================================================================

def win(process, title="some window"):
    return lambda: {"process": process, "title": title, "pid": 1}

def tab(browser, url):
    return lambda: {"browser": browser, "title": "t", "url": url}

WORK_URL = "https://docs.example.com/page"
OTHER_URL = "https://youtube.com/watch"
JARVIS_URL = "http://localhost:4700/"

print("\nTest 27: the reader reads the frontmost APP first, then the tab only for a browser")
s = fs._read_surface(tab("Opera GX", WORK_URL), win("code.exe"))
check("non-browser app in front -> 'app', even with a tracked tab behind it", s.kind == "app")
check("app identity is the app's own hash", s.identity_hash == fs._hash_identity("code.exe", None))
s = fs._read_surface(tab("Opera GX", WORK_URL), win("opera.exe"))
check("tracked browser in front -> 'tab'", s.kind == "tab")
check("tab identity is browser+host, same format as before 2.1.0",
      s.identity_hash == fs._hash_identity("Opera GX", "docs.example.com"))
s = fs._read_surface(tab("Opera GX", WORK_URL), win("chrome.exe"))
check("an UNtracked browser in front never borrows the tracked browser's tab -> 'app'",
      s.kind == "app" and s.identity_hash == fs._hash_identity("chrome.exe", None))
s = fs._read_surface(tab("Opera GX", JARVIS_URL), win("opera.exe"))
check("browser in front on the Jarvis tab -> 'jarvis' (home base)", s.kind == "jarvis" and s.is_home_base)
s = fs._read_surface(tab("Opera GX", WORK_URL), win("python.exe", "Jarvis Focus"))
check("the desktop card (python.exe) in front -> 'own' (home base)", s.kind == "own" and s.is_home_base)
s = fs._read_surface(tab("Opera GX", WORK_URL), win("WindowsTerminal.exe", "Jarvis - Server"))
check("a Jarvis console in front -> 'own' (home base)", s.kind == "own")
s = fs._read_surface(tab("Opera GX", WORK_URL), None)
check("no window reader on this platform -> tabs only, as before", s.kind == "tab")
check("nothing readable -> 'none'", fs._read_surface(None, None).kind == "none")

print("\nTest 28: switching to a non-browser app now reads as drift (invisible before 2.1.0)")
TAB_TARGET = fs._hash_identity("Opera GX", "docs.example.com")
s28 = fs.FocusSession("write the doc", 25, target_hash=TAB_TARGET)
sf = fs._read_surface(tab("Opera GX", WORK_URL), win("opera.exe"))
s28.record_tick(sf.identity_hash, sf.is_home_base, sf.app_hash)
check("on target in the locked tab", not s28.is_drifted and s28.drift_started_at is None)
sf = fs._read_surface(tab("Opera GX", WORK_URL), win("discord.exe"))  # tab behind is unchanged
s28.record_tick(sf.identity_hash, sf.is_home_base, sf.app_hash)
advance(s28, 2)
s28.record_tick(sf.identity_hash, sf.is_home_base, sf.app_hash)
check("drifted once another app is in front, even though the tab behind didn't change", s28.is_drifted)

print("\nTest 29: an app-only lock treats every tab inside that app as on target")
OPERA_APP = fs._hash_identity("opera.exe", None)
s29 = fs.FocusSession(None, 25, target_hash=OPERA_APP, target_is_app_only=True)
for url in (WORK_URL, OTHER_URL):
    sf = fs._read_surface(tab("Opera GX", url), win("opera.exe"))
    s29.record_tick(sf.identity_hash, sf.is_home_base, sf.app_hash)
    advance(s29, 2)
    s29.record_tick(sf.identity_hash, sf.is_home_base, sf.app_hash)
check("two different tabs in the locked app: no drift (was an instant drift before 2.1.0)", not s29.is_drifted)
sf = fs._read_surface(tab("Opera GX", WORK_URL), win("code.exe"))
s29.record_tick(sf.identity_hash, sf.is_home_base, sf.app_hash)
advance(s29, 2)
s29.record_tick(sf.identity_hash, sf.is_home_base, sf.app_hash)
check("a different app is still a drift", s29.is_drifted)

def fresh_current(session):
    fs.stop_session()
    time.sleep(0.05)
    fs._current_session = session
    return session

print("\nTest 30: 'lock on this tab' from a work tab locks it now and silently forgives the drift")
s30 = fresh_current(fs.FocusSession("write the doc", 25, target_hash=TAB_TARGET))
s30.record_tick(fs._hash_identity("Opera GX", "youtube.com"), False)
advance(s30, 2)
s30.record_tick(fs._hash_identity("Opera GX", "youtube.com"), False)
s30.pop_announcements()
check("drifted into youtube before re-targeting", s30.is_drifted and s30.drift_count == 1)
outcome, line = fs.retarget(tab("Opera GX", OTHER_URL), win("opera.exe"))
check("outcome is 'locked'", outcome == "locked")
check("line is exactly 'Locked on, sir.'", line == "Locked on, sir.")
check("the new tab is now the target", s30.target_hash == fs._hash_identity("Opera GX", "youtube.com"))
check("drift forgiven: no longer drifted", not s30.is_drifted)
check("drift forgiven: refunded, so it doesn't count", s30.drift_count == 0)
check("no welcome-back (or any) line queued -- it's silent", s30.pop_announcements() == [])
s30.record_tick(fs._hash_identity("Opera GX", "youtube.com"), False)
check("staying on the new target is on track", not s30.is_drifted)

print("\nTest 30b: a callout for the forgiven drift that hasn't been spoken yet is dropped")
s30b = fresh_current(fs.FocusSession(None, 25, target_hash=TAB_TARGET))
s30b.record_tick(OTHER, False)
advance(s30b, 2)
s30b.record_tick(OTHER, False)          # drift -> callout queued, NOT yet polled by the viewer
s30b.announce("Some unrelated line.")   # a non-callout line queued alongside it
fs.retarget(tab("Opera GX", OTHER_URL), win("opera.exe"))
pending = s30b.pop_announcements()
check("the queued drift callout is gone (it would have played after 'Locked on')",
      not any(m in fs.CALLOUT_TIER_1 for m in pending))
check("other queued lines survive", pending == ["Some unrelated line."])

print("\nTest 31: from the Jarvis tab it re-arms the deferred lock instead")
s31 = fresh_current(fs.FocusSession("write the doc", 25, target_hash=TAB_TARGET))
outcome, line = fs.retarget(tab("Opera GX", JARVIS_URL), win("opera.exe"))
check("outcome is 'rearmed'", outcome == "rearmed")
check("line is exactly the spec's 'Go to it, sir — I'll lock on where you land.'",
      line == "Go to it, sir — I'll lock on where you land.")
check("lock dropped and deferred again", s31.deferred and s31.target_hash is None)
s31.tick_settle(tab("Opera GX", OTHER_URL), win("opera.exe"))
s31.tick_settle(tab("Opera GX", OTHER_URL), win("opera.exe"))
check("then settles on the next surface as normal", not s31.deferred and s31.target_hash is not None)
check("and says 'Locked on, sir.' when it does", fs.LOCKED_ON_LINE in s31.pop_announcements())

print("\nTest 32: from a non-browser app it locks the app")
s32 = fresh_current(fs.FocusSession(None, 25, target_hash=TAB_TARGET))
outcome, line = fs.retarget(tab("Opera GX", WORK_URL), win("code.exe"))
check("outcome 'locked'", outcome == "locked" and line == fs.LOCKED_ON_LINE)
check("target is the app, app-only", s32.target_hash == fs._hash_identity("code.exe", None) and s32.target_is_app_only)

print("\nTest 33: THE CARD TRAP -- a click on the card never locks the card")
s33 = fresh_current(fs.FocusSession(None, 25, target_hash=TAB_TARGET))
outcome, line = fs.retarget(tab("Opera GX", OTHER_URL), win("python.exe", "Jarvis Focus"), from_card=True)
check("from the card: reads the browser's front tab directly and locks THAT", outcome == "locked")
check("target is the browser tab, not python.exe",
      s33.target_hash == fs._hash_identity("Opera GX", "youtube.com")
      and s33.target_hash != fs._hash_identity("python.exe", None))
s33b = fresh_current(fs.FocusSession(None, 25, target_hash=TAB_TARGET))
outcome, _ = fs.retarget(tab("Opera GX", OTHER_URL), win("python.exe", "Jarvis Focus"), from_card=False)
check("frontmost is our own process even without the flag: same safe path", outcome == "locked"
      and s33b.target_hash == fs._hash_identity("Opera GX", "youtube.com"))

print("\nTest 34: nothing trustworthy to lock -> say so, leave the lock alone")
s34 = fresh_current(fs.FocusSession(None, 25, target_hash=TAB_TARGET))
outcome, line = fs.retarget(None, win("python.exe", "Jarvis Focus"), from_card=True)
check("card with no readable browser -> 'blind', card-specific line",
      outcome == "blind" and line == fs.RETARGET_CARD_BLIND_LINE)
check("lock unchanged", s34.target_hash == TAB_TARGET)
outcome, line = fs.retarget(None, None)
check("voice with nothing readable -> 'blind', plain line", outcome == "blind" and line == fs.RETARGET_BLIND_LINE)

print("\nTest 35: no session running")
fs.stop_session()
time.sleep(0.05)
outcome, line = fs.retarget(tab("Opera GX", WORK_URL), win("opera.exe"))
check("outcome 'no_session'", outcome == "no_session" and line == fs.RETARGET_NO_SESSION_LINE)
fresh_ledger()

print("\nTest 36: pause/resume no longer queue a duplicate line (double-speak fix)")
s36 = fs.FocusSession(None, 25, target_hash=TAB_TARGET)
s36.pause(); s36.resume()
check("nothing queued by pause/resume", s36.pop_announcements() == [])
s36.announce("Paused, sir.")
check("announce() queues exactly what it's given (used for card actions)", s36.pop_announcements() == ["Paused, sir."])

print("\nTest 37: the report card is coloured by a short intent, never a long one")
rep = {"timestamp": 1, "planned_minutes": 25, "active_minutes": 25, "on_target_minutes": 23,
       "drifts": 1, "seconds_adrift": 60, "percent": 92, "completed": True}
line = fs.report_line(rep, "completed", "the thumbnail sprint")
check("completed report opens with \"Time's up\" and names the intent",
      line.startswith("Time's up, sir") and "'the thumbnail sprint'" in line)
line = fs.report_line(rep, "stopped", long_task)
check("a long intent is left out of the report", "quarterly" not in line)
fresh_ledger()

print("\nTest 38: a session that runs out of time speaks its report card (was silent before 2.1.0)")
fs.stop_session()
s38 = fs.start_session("the sprint", 1 / 60, get_tab_fn=tab("Opera GX", WORK_URL), get_window_fn=None)
time.sleep(2.5)
msgs38 = s38.pop_announcements()
check("session completed on its own", s38.ended_reason == "completed")
check("and queued a spoken \"Time's up\" report", any(m.startswith("Time's up, sir") for m in msgs38))
fresh_ledger()

print("\nTest 39: replacing a session leaves exactly one watcher thread running")
for _ in range(3):
    fs.start_session(None, 5, get_tab_fn=tab("Opera GX", WORK_URL), get_window_fn=None)
time.sleep(1.5)
watchers = [t for t in threading.enumerate() if t.name == "focus-watcher" and t.is_alive()]
check(f"one live watcher after three starts (found {len(watchers)})", len(watchers) == 1)
fs.stop_session()
time.sleep(1.2)
watchers = [t for t in threading.enumerate() if t.name == "focus-watcher" and t.is_alive()]
check(f"none after stop (found {len(watchers)})", len(watchers) == 0)
fresh_ledger()

# ===========================================================================
# 2.2.0 — Prompt 12 (Name the Distraction)
# ===========================================================================

def drift_into(session, identity, label, app_hash=None):
    """Drive one real drift through record_tick: open the grace window,
    pass it, and return whatever got announced."""
    session.pop_announcements()
    session.record_tick(identity, False, app_hash, label=label)
    advance(session, 2)
    session.record_tick(identity, False, app_hash, label=label)
    return session.pop_announcements()

def back_on_target(session):
    session.record_tick(session.target_hash, False)
    session.pop_announcements()

IG = fs._hash_identity("Opera GX", "www.instagram.com")
YT = fs._hash_identity("Opera GX", "www.youtube.com")

print("\nTest 40: the label -- the spec's big distractions by name, anything else as its bare domain")
cases = {
    "instagram.com": "Instagram", "www.instagram.com": "Instagram", "m.youtube.com": "YouTube",
    "youtu.be": "YouTube", "x.com": "X", "twitter.com": "X", "old.reddit.com": "Reddit",
    "www.tiktok.com": "TikTok", "www.netflix.com": "Netflix", "mail.google.com": "Gmail",
    "docs.google.com": "docs.google.com", "www.example.com:8080": "example.com",
    "news.bbc.co.uk": "news.bbc.co.uk",
}
for host, want in cases.items():
    check(f"{host} -> {want!r}", fs._label_for_host(host) == want)
check("app label = the app's own display name", fs._label_for_window({"process": "discord.exe", "display_name": "Discord"}) == "Discord")
check("app label falls back to the exe name without .exe", fs._label_for_window({"process": "notepad.exe"}) == "Notepad")
check("unknown process -> no label (nameless line)", fs._label_for_window({"process": "unknown"}) is None)

print("\nTest 41: the reader carries a label for work surfaces, never for home base")
check("tab surface -> site label", fs._read_surface(tab("Opera GX", "https://www.instagram.com/reels"), win("opera.exe")).label == "Instagram")
check("app surface -> app label",
      fs._read_surface(tab("Opera GX", WORK_URL), lambda: {"process": "discord.exe", "title": "x", "display_name": "Discord"}).label == "Discord")
check("the Jarvis tab never gets a label", fs._read_surface(tab("Opera GX", JARVIS_URL), win("opera.exe")).label is None)
check("the desktop card never gets a label", fs._read_surface(tab("Opera GX", WORK_URL), win("python.exe", "Jarvis Focus")).label is None)

print("\nTest 42: a drift into a mapped site names it -- \"Sir, Instagram can wait.\"")
s42 = fs.FocusSession(None, 25, target_hash=TAB_TARGET)
msgs = drift_into(s42, IG, "Instagram")
check("one callout, from the named tier-1 pool with the name in it",
      len(msgs) == 1 and msgs[0] in [l.format(label="Instagram") for l in fs.NAMED_TIER_1])
print(f"         -> {msgs[0]!r}")

print("\nTest 43: the session's first callout names the work AND the distraction")
s43 = fs.FocusSession("the thumbnail sprint", 25, target_hash=TAB_TARGET)
msgs = drift_into(s43, IG, "Instagram")
check("from the named-intent pool",
      msgs and msgs[0] in [l.format(label="Instagram", intent="the thumbnail sprint") for l in fs.NAMED_INTENT_FIRST])
print(f"         -> {msgs[0]!r}")

print("\nTest 44: repeat drifts into the same place are counted -- by hash, not by name")
s44 = fs.FocusSession(None, 25, target_hash=TAB_TARGET)
drift_into(s44, YT, "YouTube"); back_on_target(s44)
msgs2 = drift_into(s44, YT, "YouTube"); back_on_target(s44)
msgs3 = drift_into(s44, YT, "YouTube"); back_on_target(s44)
msgs_other = drift_into(s44, IG, "Instagram")
check("second visit says 'Twice'", msgs2 and "Twice" in msgs2[0] and "YouTube" in msgs2[0])
check("third visit says 'Three times'", msgs3 and "Three times" in msgs3[0])
check("a different site starts fresh at tier one",
      msgs_other and msgs_other[0] in [l.format(label="Instagram") for l in fs.NAMED_TIER_1])
check("the counts are kept by hash only (no names as keys)", set(s44._drift_visits) == {YT, IG})
print(f"         -> {msgs2[0]!r}\n         -> {msgs3[0]!r}")

print("\nTest 45: a long named drift escalates and rotates -- never loops one phrase")
s45 = fs.FocusSession(None, 25, target_hash=TAB_TARGET)
drift_into(s45, YT, "YouTube")
lines = []
for _ in range(5):
    advance(s45, fs.DEFAULT_NAG_INTERVAL_SECONDS + 1)
    s45.record_tick(YT, False, label="YouTube")
    lines += s45.pop_announcements()
check("first nag is named tier 2", lines[0] in [l.format(label="YouTube") for l in fs.NAMED_TIER_2])
check("then named tier 3", lines[1] in [l.format(label="YouTube") for l in fs.NAMED_TIER_3])
check("four nags at tier 3 are four different lines", len(set(lines[1:5])) == 4)

print("\nTest 46: four lines per escalation tier, in every pool")
pools = {
    "nameless": fs.CALLOUT_TIERS, "named": fs.NAMED_TIERS, "drill named": fs.DRILL_NAMED_TIERS,
}
for name, tiers in pools.items():
    check(f"{name}: 3 tiers x 4 lines", len(tiers) == 3 and all(len(t) == 4 for t in tiers))
for name, pool in {"repeat": fs.NAMED_REPEAT, "named intent": fs.NAMED_INTENT_FIRST,
                   "intent": fs.INTENT_FIRST_CALLOUT, "drill repeat": fs.DRILL_NAMED_REPEAT,
                   "drill intent": fs.DRILL_NAMED_INTENT_FIRST, "drill nameless": fs.DRILL_NAMELESS}.items():
    check(f"{name}: 4 lines", len(pool) == 4)

print("\nTest 47: drill-sergeant mode names it too, and switches back off")
fs.set_drill_mode(True)
s47 = fs.FocusSession(None, 25, target_hash=TAB_TARGET)
msgs = drift_into(s47, IG, "Instagram")
check("drill line, named", msgs and msgs[0] in [l.format(label="Instagram") for l in fs.DRILL_NAMED_TIER_1])
print(f"         -> {msgs[0]!r}")
msgs = drift_into(fs.FocusSession(None, 25, target_hash=TAB_TARGET), OTHER, None)
check("drill line, nameless fallback", msgs and msgs[0] in fs.DRILL_NAMELESS)
fs.set_drill_mode(False)
msgs = drift_into(fs.FocusSession(None, 25, target_hash=TAB_TARGET), IG, "Instagram")
check("off again -> normal named line", msgs and msgs[0] in [l.format(label="Instagram") for l in fs.NAMED_TIER_1])

print("\nTest 48: the switch at the top of the file turns naming off")
fs.NAME_DISTRACTIONS = False
msgs = drift_into(fs.FocusSession(None, 25, target_hash=TAB_TARGET), IG, "Instagram")
check("nameless line even though a name was available", msgs and msgs[0] in fs.CALLOUT_TIER_1)
check("and the name is nowhere in it", "Instagram" not in msgs[0])
fs.NAME_DISTRACTIONS = True

print("\nTest 49: re-targeting away from a drift refunds its visit too")
s49 = fresh_current(fs.FocusSession(None, 25, target_hash=TAB_TARGET))
drift_into(s49, YT, "YouTube")
fs.retarget(tab("Opera GX", WORK_URL), win("opera.exe"))  # forgive it
s49.target_hash = TAB_TARGET
msgs = drift_into(s49, YT, "YouTube")
check("the forgiven drift doesn't count: next one is a first visit, not 'Twice'",
      msgs and "Twice" not in msgs[0] and msgs[0] in [l.format(label="YouTube") for l in fs.NAMED_TIER_1])
fs.stop_session(); time.sleep(0.05); fresh_ledger()

print("\nTest 50: THE PRIVACY LAW -- a made-up label rides one tick into the line and is stored nowhere")
# Names nobody would ever write, so this can't be fooled by example
# sentences elsewhere in the code (a grep for "Instagram" would be).
FAKE_SITE = "zqvfrimbleworth-7731.test"
FAKE_APP = "Qworlbex Snarfle Studio"
surface_now = {"kind": "work"}
def fake_tab():
    url = {"work": WORK_URL, "site": f"https://{FAKE_SITE}/page"}.get(surface_now["kind"], WORK_URL)
    return {"browser": "Opera GX", "title": "t", "url": url}
def fake_win():
    if surface_now["kind"] == "app":
        return {"process": "qworlbex.exe", "title": "a window", "pid": 9, "display_name": FAKE_APP}
    return {"process": "opera.exe", "title": "a window", "pid": 8, "display_name": "Opera Internet Browser"}

fs.stop_session(); time.sleep(0.05); fresh_ledger()
s50 = fs.start_session("privacy test", 5, get_tab_fn=fake_tab, get_window_fn=fake_win)
spoken = []
for kind in ("site", "work", "app"):
    surface_now["kind"] = kind
    time.sleep(2.6)                      # real watcher thread, real ticks
    spoken += s50.pop_announcements()
surface_now["kind"] = "work"
time.sleep(1.5)
spoken += s50.pop_announcements()
print("         spoken:", spoken)
check("the made-up SITE name drove a spoken line", any(FAKE_SITE in m for m in spoken))
check("the made-up APP name drove a spoken line", any(FAKE_APP in m for m in spoken))

def dump(obj):
    """Everything reachable from the session's own attributes, as text."""
    out = []
    for k, v in vars(obj).items():
        if k == "_lock":
            continue
        out.append(f"{k}={v!r}")
    return "\n".join(out)

for label in (FAKE_SITE, FAKE_APP, "zqvfrimbleworth", "Qworlbex"):
    check(f"{label!r} absent from session state (every attribute)", label not in dump(s50))
    check(f"{label!r} absent from the status sent to the browser", label not in json.dumps(s50.status_dict()))
    check(f"{label!r} absent from the report card data", label not in json.dumps(s50.report()))
fs.stop_session(reason="stopped")
time.sleep(0.2)
ledger_text = open(fs.LEDGER_PATH).read() if os.path.exists(fs.LEDGER_PATH) else ""
check("the ledger was written (so the next check means something)", "privacy" not in ledger_text and len(ledger_text) > 0)
notes_root = os.path.join(os.path.dirname(os.path.abspath(fs.__file__)), "notes")
notes_text = ""
for root, _dirs, files in os.walk(notes_root):
    for f in files:
        try:
            notes_text += open(os.path.join(root, f), encoding="utf-8", errors="ignore").read()
        except OSError:
            pass
for label in (FAKE_SITE, FAKE_APP):
    check(f"{label!r} absent from the ledger on disk", label not in ledger_text)
    check(f"{label!r} absent from every note (the assistant's notes)", label not in notes_text)
    check(f"{label!r} absent from the report line spoken at the end",
          label not in fs.report_line(s50.report(), "stopped", s50.task))
check("nothing left waiting to be spoken either", s50.pop_announcements() == [])
fresh_ledger()

print(f"\n{PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)
