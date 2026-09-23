"""
Real-execution test of focus_overlay.py's non-Tk logic: HTTP polling and
status formatting. Run with the real server up (python3 server.py) so
fetch_status hits a real endpoint, not a mock.
"""

import sys
import focus_overlay as ov

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


print("Test 1: format_time")
check("0s -> 00:00", ov.format_time(0) == "00:00")
check("65s -> 01:05", ov.format_time(65) == "01:05")
check("negative clamps to 00:00", ov.format_time(-5) == "00:00")

print("\nTest 2: overlay_text pure formatting")
title, detail, color = ov.overlay_text(None, "connection refused")
check("error state shows error color", color == "error")

title, detail, color = ov.overlay_text({"status": {"active": False}}, None)
check("idle state when no session active", color == "idle")

on_track_data = {"status": {"active": True, "task": "Acme brief", "remaining_seconds": 125, "is_drifted": False}}
title, detail, color = ov.overlay_text(on_track_data, None)
check("on_track color when not drifted", color == "on_track")
check("countdown shows correctly formatted time", "02:05" in title)
check("detail names the task", "Acme brief" in detail)

drifted_data = {"status": {"active": True, "task": "Acme brief", "remaining_seconds": 60, "is_drifted": True}}
title, detail, color = ov.overlay_text(drifted_data, None)
check("drifted color when is_drifted true", color == "drifted")
check("detail flags drifted state", "Drifted" in detail)

print("\nTest 3: fetch_status against the REAL live server (must be running)")
data, error = ov.fetch_status()
check("fetch_status reaches the real server with no error", error is None)
check("response has a status key", data is not None and "status" in data)

print("\nTest 4: fetch_status against an unreachable server degrades gracefully")
data2, error2 = ov.fetch_status(server_url="http://localhost:19999", timeout=0.5)
check("no exception raised, error string returned instead", data2 is None and error2 is not None)

print("\nTest 5: card_controls (Prompt 11) -- greyed out with no session, pause/resume toggles")
ctl = ov.card_controls(None, "refused")
check("server unreachable -> controls disabled", ctl["enabled"] is False)
ctl = ov.card_controls({"status": {"active": False}}, None)
check("no session -> controls disabled", ctl["enabled"] is False)
ctl = ov.card_controls({"status": {"active": True, "paused": False}}, None)
check("running -> enabled, button says PAUSE and sends the pause phrase",
      ctl["enabled"] and "PAUSE" in ctl["pause_label"] and ctl["pause_message"] == ov.CARD_PAUSE_MESSAGE)
ctl = ov.card_controls({"status": {"active": True, "paused": True}}, None)
check("paused -> button says RESUME and sends the resume phrase",
      "RESUME" in ctl["pause_label"] and ctl["pause_message"] == ov.CARD_RESUME_MESSAGE)

print("\nTest 6: the LOCK THIS TAB pill only flashes LOCKED when it actually locked")
check("locked -> LOCKED", ov.flash_label({"outcome": "locked"}, None) == "LOCKED")
check("rearmed -> not LOCKED", ov.flash_label({"outcome": "rearmed"}, None) != "LOCKED")
check("blind -> not LOCKED", ov.flash_label({"outcome": "blind"}, None) != "LOCKED")
check("no_session -> not LOCKED", ov.flash_label({"outcome": "no_session"}, None) != "LOCKED")
check("server unreachable -> OFFLINE", ov.flash_label(None, "refused") == "OFFLINE")

print("\nTest 7: every card button's phrase is parsed as the command it's meant to be")
import focus_commands as fc
check("pause phrase -> pause", fc.is_pause_command(ov.CARD_PAUSE_MESSAGE))
check("resume phrase -> resume (and not pause)",
      fc.is_resume_command(ov.CARD_RESUME_MESSAGE) and not fc.is_pause_command(ov.CARD_RESUME_MESSAGE))
check("re-target phrase -> re-target", fc.is_retarget_command(ov.CARD_RETARGET_MESSAGE))
check("abort phrase -> abort (not stop)",
      fc.is_abort_command(ov.CARD_ABORT_MESSAGE) and not fc.is_stop_command(ov.CARD_ABORT_MESSAGE))

print("\nTest 8: the card's buttons against the REAL live server (must be running)")
import json as _json
import urllib.request as _ur

def _get(path):
    with _ur.urlopen(f"{ov.SERVER_URL}{path}", timeout=2) as r:
        return _json.loads(r.read().decode("utf-8"))

def _post(path, payload):
    req = _ur.Request(f"{ov.SERVER_URL}{path}", data=_json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
    with _ur.urlopen(req, timeout=2) as r:
        return _json.loads(r.read().decode("utf-8"))

_post("/focus/command", {"message": "abort focus session"})
_get("/focus/announcements")  # drain anything left over
data, error = ov.post_command(ov.CARD_RETARGET_MESSAGE)
check("LOCK THIS TAB with no session -> outcome no_session", error is None and data.get("outcome") == "no_session")

_post("/focus/command", {"message": "start a focus session for 5 minutes"})
_get("/focus/announcements")
data, error = ov.post_command(ov.CARD_PAUSE_MESSAGE)
check("card PAUSE pauses the real session", error is None and data["status"]["paused"] is True)
spoken = _get("/focus/announcements")["announcements"]
check("and queues its reply for the Jarvis tab to speak (the card has no voice)", spoken == ["Paused, sir."])
data, error = ov.post_command(ov.CARD_RESUME_MESSAGE)
check("card RESUME resumes it", error is None and data["status"]["paused"] is False)
_get("/focus/announcements")
data, error = ov.post_command(ov.CARD_RETARGET_MESSAGE)
check("card LOCK THIS TAB returns a re-target outcome", error is None and data.get("outcome") in ("locked", "rearmed", "blind"))
spoken = _get("/focus/announcements")["announcements"]
check("and its reply is queued to be spoken", len(spoken) == 1 and spoken[0] == data["answer"])
data, error = ov.post_command(ov.CARD_ABORT_MESSAGE)
check("card ABORT ends the session", error is None and data["status"]["active"] is False)
_get("/focus/announcements")

print("\nTest 9: a voice/typed command (no card flag) is NOT also queued -- no double-speak")
_post("/focus/command", {"message": "start a focus session for 5 minutes"})
_get("/focus/announcements")
reply = _post("/focus/command", {"message": "pause my focus session"})
spoken = _get("/focus/announcements")["announcements"]
check("reply comes back directly", reply["answer"] == "Paused, sir.")
check("and nothing is queued to be said a second time", spoken == [])
_post("/focus/command", {"message": "abort focus session"})
_get("/focus/announcements")

print("\nTest 10: post_command against an unreachable server degrades gracefully")
data, error = ov.post_command("pause my focus session", server_url="http://localhost:19999", timeout=0.5)
check("no exception raised, error string returned instead", data is None and error is not None)

print(f"\n{PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)
