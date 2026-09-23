#!/usr/bin/env python3
"""
preflight.py — end-to-end health check for the RUNNING Jarvis server.

Not unit tests. Real HTTP calls against whatever is actually serving at
http://localhost:4700 right now, because the failures that hurt are the
ones where every unit test passes and the real chain is dead.

Run:  python3 preflight.py   (with server.py already running in another
terminal — this checks the live thing, not the source code)
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

import records
import server  # reuses config/constants/helpers; importing does NOT start the server

BASE_URL = "http://localhost:4700"

# A genuinely valid, tiny (1x1 white pixel) JPEG — real image bytes, not a
# placeholder string, so /see is exercised the same way a real screenshot
# would exercise it.
TEST_JPEG_BASE64 = (
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAMCAgICAgMCAgIDAwMDBAYEBAQEBAgGBgUGCQgKCgkI"
    "CQkKDA8MCgsOCwkJDRENDg8QEBEQCgwSExIQEw8QEBD/wAALCAABAAEBAREA/8QAFAABAAAAAAAA"
    "AAAAAAAAAAAAAP/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AVN//2Q=="
)

results = []


def check(name, fn):
    try:
        status, detail = fn()
    except Exception as e:
        status, detail = "FAIL", f"raised {type(e).__name__}: {e}"
    results.append((name, status, detail))
    icon = {"PASS": "\u2713", "FAIL": "\u2717", "WARN": "!"}[status]
    print(f"  [{icon}] {name}" + (f" \u2014 {detail}" if detail else ""))
    return status, detail


def http_get(path):
    """Never raises on 4xx/5xx — some checks WANT to see a 404."""
    try:
        with urllib.request.urlopen(BASE_URL + path, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        raise RuntimeError(f"could not connect to {BASE_URL} — is server.py running? ({e.reason})")


def http_post(path, payload):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        BASE_URL + path, data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except (json.JSONDecodeError, ValueError):
            return e.code, {}
    except urllib.error.URLError as e:
        raise RuntimeError(f"could not connect to {BASE_URL} — is server.py running? ({e.reason})")


def openrouter_probe(key, model):
    """One minimal real call — the only way to actually know a key/model works."""
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps({"model": model, "messages": [{"role": "user", "content": "Reply with OK."}]}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.status


# ---------------------------------------------------------------------------

def check_server_up():
    status, body = http_get("/")
    if status == 200 and b"<html" in body.lower():
        return "PASS", None
    return "FAIL", f"unexpected response (HTTP {status})"


def check_graph_loads():
    try:
        graph = server.load_graph()
    except Exception as e:
        return "FAIL", f"could not load graph-data.js: {e}"
    n = len(graph.get("nodes", []))
    # 2.3.0: zero notes is a real state now (the demo notes live in
    # examples/). What matters is that the galaxy matches the notes folder.
    on_disk = len(records.note_files(server.NOTES_DIR))
    if n != on_disk:
        return "FAIL", f"{n} nodes in the galaxy but {on_disk} notes in the folder"
    return "PASS", f"{n} nodes" if n else "no notes yet (empty galaxy, as expected)"


def check_files_served_match_disk():
    _, served_html = http_get("/")
    with open(os.path.join(server.VIEWER_DIR, "index.html"), "rb") as f:
        disk_html = f.read()
    if served_html != disk_html:
        return "FAIL", "served index.html does not match the file on disk — stale process?"

    _, served_js = http_get("/graph-data.js")
    with open(server.GRAPH_DATA_PATH, "rb") as f:
        disk_js = f.read()
    if served_js != disk_js:
        return "FAIL", "served graph-data.js does not match the file on disk"
    return "PASS", None


def check_config_not_browser_reachable():
    for path in ("/config.json", "/../config.json", "/%2e%2e/config.json", "/..%2fconfig.json"):
        status, _ = http_get(path)
        if status == 200:
            return "FAIL", f"{path} returned 200 \u2014 config.json IS reachable from the browser"
    return "PASS", None


def check_api_key_valid():
    try:
        config = server.load_config()
    except Exception as e:
        return "FAIL", f"could not read config.json: {e}"
    key = config.get("openrouter_api_key", "")
    if key in ("", server.PLACEHOLDER_KEY):
        return "WARN", "placeholder key \u2014 add a real OpenRouter key to test this"
    try:
        status = openrouter_probe(key, "openrouter/free")
        return ("PASS", None) if status == 200 else ("WARN", f"unexpected HTTP {status}")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return "FAIL", f"key rejected (HTTP {e.code}) \u2014 check openrouter_api_key in config.json"
        return "WARN", f"HTTP {e.code} on a real call \u2014 may be transient"
    except urllib.error.URLError as e:
        return "FAIL", f"could not reach OpenRouter: {e.reason}"


def check_model_chain_reachable():
    try:
        config = server.load_config()
    except Exception as e:
        return "FAIL", f"could not read config.json: {e}"
    key = config.get("openrouter_api_key", "")
    if key in ("", server.PLACEHOLDER_KEY):
        return "WARN", "placeholder key \u2014 add a real key to test this"

    chain = config.get("model_chain") or server.DEFAULT_MODEL_CHAIN
    reachable, unreachable = [], []
    for model in chain:
        try:
            if openrouter_probe(key, model) == 200:
                reachable.append(model)
            else:
                unreachable.append(model)
        except (urllib.error.HTTPError, urllib.error.URLError):
            unreachable.append(model)

    if not reachable:
        return "FAIL", f"none of {chain} were reachable \u2014 check model_chain in config.json"
    if unreachable:
        return "WARN", f"{len(reachable)}/{len(chain)} reachable \u2014 stale: {', '.join(unreachable)}"
    return "PASS", f"{len(reachable)}/{len(chain)} reachable"


def check_remember_round_trip():
    marker = f"preflightmarker{int(time.time())}"
    said = f"Remember that this is a preflight test note {marker}"
    _, data = http_post("/remember", {"message": said, "source": "typed"})
    if not data.get("ok"):
        return "FAIL", f"/remember failed: {data.get('error')}"
    note_path = data["node"]["path"]
    record_id = data["node"].get("record_id")

    # Test retrievability directly through the scoring function — this
    # doesn't need a working API key, since indexing and retrieval are
    # independent of whether the brain itself can answer.
    try:
        graph = server.load_graph()
        matches = server.score_notes(f"tell me about {marker}", graph["nodes"])
        found = any(n.get("path") == note_path for n in matches)
        # 2.3.0: its record holds the exact words, and the index finds it.
        record = records.find_by_path(note_path, server.NOTES_DIR)
        record_ok = bool(record) and record["id"] == record_id and record["original_input"] == said \
            and record["source"] == "typed"
        indexed = any(h[1] == records.rel_path(note_path, server.NOTES_DIR)
                      for h in records.search(marker, notes_dir=server.NOTES_DIR))
    finally:
        # A preflight run shouldn't leave junk in the real notes folder --
        # the note, its record, or a "note missing" mark on that record.
        try:
            os.remove(note_path)
            if record_id:
                records.forget(record_id, server.NOTES_DIR)
            server.rebuild_graph()
        except OSError:
            pass

    if data.get("warning"):
        return "FAIL", data["warning"]
    if not found:
        return "FAIL", "captured note was not retrievable immediately after writing"
    if not record_ok:
        return "FAIL", "the capture's record is missing or doesn't hold the exact words said"
    if not indexed:
        return "FAIL", "the capture wasn't found by the full-text index"
    return "PASS", None


def _cleanup_capture(note_path, record_id):
    """Removes a capture preflight made: the note and its record (the
    caller rebuilds afterwards), so nothing is left behind."""
    try:
        os.remove(note_path)
    except OSError:
        pass
    if record_id:
        records.forget(record_id, server.NOTES_DIR)


def check_capture_mode_round_trip():
    """2.4.0: capture mode keeps the words exactly, into the INBOX."""
    marker = f"prflt{int(time.time())}"
    said = f"Felt a bit scattered today, {marker}, not sure why."
    _, data = http_post("/remember", {"message": said, "source": "typed", "mode": "capture-mode"})
    if not data.get("ok"):
        return "FAIL", f"capture-mode capture failed: {data.get('error')}"
    note_path, record_id = data["node"]["path"], data["node"].get("record_id")
    try:
        with open(note_path, encoding="utf-8") as f:
            verbatim = f.read().endswith(f"\n\n{said}\n")
        _, raw = http_get("/inbox")
        listed = any(marker in i["preview"] for i in json.loads(raw)["items"])
    finally:
        _cleanup_capture(note_path, record_id)
        server.rebuild_graph()
    if not verbatim:
        return "FAIL", "capture mode didn't keep the words exactly"
    if not listed:
        return "FAIL", "the capture didn't show up in /inbox"
    return "PASS", None


def check_paper_round_trip():
    """2.4.0: a real (tiny, blank) photo through /paper/extract -- a real
    call to the vision model when there's a key -- then /paper/save, then
    everything it made is removed again."""
    import base64
    import struct
    import zlib
    raw = b"".join(b"\x00" + b"\xff\xff\xff" * 8 for _ in range(8))
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    b64 = base64.b64encode(png).decode()

    _, ext = http_post("/paper/extract", {"image_base64": b64})
    reading = "WARN-no-read"
    if ext.get("ok"):
        reading = f"read by {ext.get('model_used')}"
    elif ext.get("reason") == "no_key":
        reading = "no key: skipped the model"
    elif ext.get("reason") == "model":
        reading = f"model couldn't read it: {ext.get('error', '')[:120]}"
    else:
        return "FAIL", f"/paper/extract: {ext.get('error')}"

    paper_dir = os.path.join(server.NOTES_DIR, "paper")
    had_folder = os.path.isdir(paper_dir)
    before = set(os.listdir(paper_dir)) if had_folder else set()
    _, saved = http_post("/paper/save", {"photo_base64": b64, "final_text": "preflight paper test",
                                         "extracted_text": ext.get("text"), "model_used": ext.get("model_used")})
    if not saved.get("ok"):
        return "FAIL", f"/paper/save: {saved.get('error')}"
    note_path, record_id = saved["node"]["path"], saved["node"].get("record_id")
    try:
        rec = records.find_by_path(note_path, server.NOTES_DIR)
        photo = os.path.join(server.NOTES_DIR, *rec["source_file"].split("/")) if rec else None
        ok = bool(rec) and rec["source"] == "paper" and photo and open(photo, "rb").read() == png
    finally:
        for f in set(os.listdir(paper_dir)) - before:
            try:
                os.remove(os.path.join(paper_dir, f))
            except OSError:
                pass
        if record_id:
            records.forget(record_id, server.NOTES_DIR)
        if not had_folder:
            try:
                os.rmdir(paper_dir)
            except OSError:
                pass
        server.rebuild_graph()
    if not ok:
        return "FAIL", "the saved page's record or photo wasn't right"
    if ext.get("ok"):
        return "PASS", reading
    return "WARN", reading + " (saving with the photo works)"


def check_sorting_round_trip():
    """2.5.0: one throwaway note sorted by the real brain (when there's a
    key): its reading is stored beside it, the note isn't changed, and a
    wish never becomes a task. Removed again afterwards; no links made."""
    marker = f"prflt{int(time.time())}"
    said = f"remember that I'd love to visit Japan someday {marker}"
    _, data = http_post("/remember", {"message": said, "source": "typed"})
    if not data.get("ok"):
        return "FAIL", f"couldn't make the test note: {data.get('error')}"
    note_path, record_id = data["node"]["path"], data["node"].get("record_id")
    try:
        with open(note_path, "rb") as f:
            before = f.read()
        _, r = http_post("/sort", {"record_ids": [record_id], "link": False})
        rec = records.find_by_path(note_path, server.NOTES_DIR)
        with open(note_path, "rb") as f:
            unchanged = f.read() == before
    finally:
        _cleanup_capture(note_path, record_id)
        server.rebuild_graph()
    if not r.get("ok"):
        if "API key" in (r.get("error") or ""):
            return "WARN", "no key: sorting not tried (it refused plainly, nothing changed)"
        return "WARN", f"the brain couldn't sort just now: {(r.get('error') or r.get('spoken') or '')[:140]}"
    if not unchanged:
        return "FAIL", "sorting changed the note itself"
    readings = [i for i in (rec or {}).get("interpretations", []) if i.get("kind") == "sorting"]
    if not readings:
        return "WARN", f"the brain answered but didn't read the test note: {r.get('spoken', '')[:120]}"
    v = readings[-1]["value"]
    if any(k in v["kinds"] for k in ("task", "commitment", "reminder")) or rec["home"] == "NOW":
        # A model judgement, not a code fault: the code only holds tasks back
        # when the model says you didn't mean to act. Flagged, not failed.
        return "WARN", (f"{readings[-1]['model']} read a wish as something to do ({', '.join(v['kinds'])} "
                        f"in {rec['home']}); you'd correct that from the inbox panel")
    return "PASS", f"read by {readings[-1]['model']}: {rec['status']}, {v['home']}, {', '.join(v['kinds']) or 'no kinds'}"


def check_reviews():
    """2.7.0: all four reviews build from the real notes without changing
    any of them, and the review scheduler is running."""
    before = {rel: records.sha256_file(records.abs_path(rel, server.NOTES_DIR))
              for rel in records.note_files(server.NOTES_DIR)}
    for kind in ("morning", "evening", "weekly", "forgotten"):
        _, raw = http_get(f"/reviews/build?kind={kind}")
        d = json.loads(raw)
        if not d.get("ok") or not d.get("spoken"):
            return "FAIL", f"the {kind} review didn't build: {d.get('error')}"
    after = {rel: records.sha256_file(records.abs_path(rel, server.NOTES_DIR))
             for rel in records.note_files(server.NOTES_DIR)}
    if before != after:
        return "FAIL", "building a review changed a note"
    _, raw = http_get("/diag")
    st = json.loads(raw).get("reviews", {})
    if not st.get("running"):
        return "WARN", "reviews build, but the scheduler isn't running (started with 'python server.py'?)"
    if st.get("popup_error") == "not Windows":
        return "PASS", "all four reviews build; scheduler running (pop-ups appear only on Windows)"
    if st.get("popup_error"):
        return "WARN", f"the last Windows pop-up failed: {st['popup_error']}"
    if st.get("settings_problem"):
        return "WARN", st["settings_problem"]
    return "PASS", "morning, evening, weekly and forgotten all build; scheduler running"


def check_records_integrity():
    """2.3.0: every note has exactly one record, the index matches, and a
    full sync changes no note by a single byte."""
    before = {rel: records.sha256_file(records.abs_path(rel, server.NOTES_DIR))
              for rel in records.note_files(server.NOTES_DIR)}
    report = records.sync_and_index(server.NOTES_DIR)
    after = {rel: records.sha256_file(records.abs_path(rel, server.NOTES_DIR))
             for rel in records.note_files(server.NOTES_DIR)}
    if before != after:
        return "FAIL", "a note's bytes changed during a records sync"
    d = records.diag(server.NOTES_DIR)
    if not d["healthy"]:
        problems = {k: d[k] for k in ("notes_without_record", "notes_with_two_records", "unreadable_record_files") if d[k]}
        return "FAIL", f"records not healthy: {problems or d.get('index')}"
    note = f"{d['notes']} notes, {d['records']} records, search: {d['index']['search']}"
    if report["edited"]:
        note += f"; noted outside edits to {len(report['edited'])} (kept, logged)"
    return "PASS", note


def check_diag_endpoint():
    status, body = http_get("/diag")
    if status != 200:
        return "FAIL", f"/diag answered HTTP {status}"
    data = json.loads(body)
    if "records" not in data or "usage" not in data:
        return "FAIL", "/diag is missing its records/usage sections"
    rec = data["records"]["status"].get("last_error")
    if rec:
        return "FAIL", f"records error since start: {rec['error']}"
    usage = data["usage"]
    if usage.get("settings_problem"):
        return "WARN", usage["settings_problem"]
    if not usage["recorder"].get("running"):
        return "WARN", "usage tracker isn't running (was the server started with 'python server.py'?)"
    if not usage["enabled"]:
        return "PASS", "usage tracking switched off in usage/settings.json"
    return "PASS", f"usage tracker running; {usage['raw_days']} raw day(s), reports {usage['reports']}"


def check_usage_store_names_only():
    """2.3.0: reads (never changes) the real usage data, and fails if any
    page address ever got into it -- names only is the promise."""
    import usage_tracker
    folder = usage_tracker.USAGE_DIR
    leaks = []
    for sub in ("raw", os.path.join("reports", "weekly"), os.path.join("reports", "monthly"),
                os.path.join("reports", "yearly")):
        d = os.path.join(folder, sub)
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if fn.endswith(".json"):
                with open(os.path.join(d, fn), encoding="utf-8") as f:
                    if "://" in f.read():
                        leaks.append(f"{sub}/{fn}")
    if leaks:
        return "FAIL", f"page addresses found in: {', '.join(leaks)}"
    return "PASS", "no page addresses in the stored usage data"


def check_see_endpoint():
    _, data = http_post("/see", {"message": "what color is this?", "image_base64": TEST_JPEG_BASE64})
    error = data.get("error", "")
    if error and "API key" in error:
        return "WARN", "placeholder key \u2014 add a real key to fully test /see"
    if error:
        return "FAIL", error
    return ("PASS", None) if "answer" in data else ("FAIL", "malformed /see response")


def check_focus_session_lifecycle():
    """
    Real start -> status -> stop round trip against the live server,
    through the single /focus/command dispatcher, exercising
    focus_session.py + focus_commands.py + the HTTP routes together.
    Cleans up after itself so a preflight run never leaves a session
    running against the real server.
    """
    # Checked BEFORE the try: the finally below aborts whatever session is
    # running, so returning from inside it would abort the user's real one
    # (the pre-2.2.0 behaviour, despite this message saying it skipped).
    _, before = http_get("/focus/status")
    before = json.loads(before)
    if before.get("status", {}).get("active"):
        return "WARN", "a focus session was already running \u2014 skipped to avoid disrupting it"
    try:
        status, data = http_post("/focus/command", {
            "message": "start a focus session on preflight testing for 1 minutes"
        })
        if status != 200 or not data.get("ok"):
            return "FAIL", f"start command failed: {data.get('error', data)}"
        if not data["status"]["active"]:
            return "FAIL", "start command returned but session is not active"
        if data["status"]["task"] != "preflight testing":
            return "FAIL", f"task text not parsed correctly: got {data['status']['task']!r}"

        _, status_data = http_get("/focus/status")
        status_data = json.loads(status_data)
        if not status_data["status"]["active"]:
            return "FAIL", "/focus/status shows inactive right after starting"

        # Exercise pause/resume through the real HTTP path too.
        _, pause_data = http_post("/focus/command", {"message": "pause my focus session"})
        if not pause_data["status"]["paused"]:
            return "FAIL", "pause command did not set paused=True"
        _, resume_data = http_post("/focus/command", {"message": "resume my session"})
        if resume_data["status"]["paused"]:
            return "FAIL", "resume command did not clear paused"

        status2, stop_data = http_post("/focus/command", {"message": "stop my focus session"})
        if status2 != 200 or not stop_data.get("ok"):
            return "FAIL", f"stop command failed: {stop_data.get('error', stop_data)}"
        if stop_data["status"]["active"]:
            return "FAIL", "session still shows active after stop command"

        return "PASS", None
    finally:
        try:
            http_post("/focus/command", {"message": "abort focus session"})
        except Exception:
            pass


def check_focus_privacy_over_http():
    """
    The spec's privacy rule ('state client sees is a whitelist of
    booleans and counters') is checked against the REAL wire response,
    not just the in-process dict \u2014 this is what actually matters,
    since the whole point is that nothing leaks over HTTP.
    """
    import focus_session as fs
    _, before = http_get("/focus/status")
    if json.loads(before).get("status", {}).get("active"):
        return "WARN", "a focus session was already running \u2014 skipped to avoid replacing it"
    try:
        http_post("/focus/command", {"message": "start a focus session for 1 minutes"})
        _, raw_status = http_get("/focus/status")
        raw_text = raw_status.decode("utf-8")

        forbidden_substrings = ["chrome", "firefox", "opera", "edge", "brave", ".com", ".org", "http://", "https://"]
        leaked = [s for s in forbidden_substrings if s in raw_text.lower()]
        if leaked:
            return "FAIL", f"raw /focus/status response contains suspicious substrings: {leaked}"

        session = fs.get_current_session()
        if session and session.target_hash and session.target_hash in raw_text:
            return "FAIL", "the internal identity hash itself leaked into the HTTP response"

        return "PASS", "no app/site identity or hash found in the wire response"
    finally:
        try:
            http_post("/focus/command", {"message": "abort focus session"})
        except Exception:
            pass


def check_focus_drift_and_escalation():
    """
    Verifies the identity-hash drift model actually flags drift when the
    tracked identity changes, that it stays quiet inside the 800ms grace
    window, and that repeated nags escalate through the three canned
    tiers \u2014 using a controllable fake reader injected straight into
    focus_session (bypassing the real tab_watcher/windows_focus OS calls,
    which this sandbox can't exercise), the same code path server.py
    wires up otherwise.
    """
    import focus_session as fs

    fs.stop_session()
    current = {"host": "docs.example.com"}
    fake_tab = lambda: {"browser": "Chrome", "title": "x", "url": f"https://{current['host']}/"}

    session = fs.start_session("preflight drift test", 5, get_tab_fn=fake_tab, get_window_fn=None)
    try:
        time.sleep(1.2)
        if session.is_drifted:
            return "FAIL", "flagged drift while identity matched the locked target"

        current["host"] = "youtube.com"
        time.sleep(fs.DRIFT_GRACE_SECONDS + 1.5)
        if not session.is_drifted:
            return "FAIL", "did not flag drift after the identity changed past the grace period"

        msgs = session.pop_announcements()
        # The session's first callout names the intent (Prompt 10) and,
        # since 2.2.0, the distraction (Prompt 12): the drift here is into
        # youtube.com, so it should say "YouTube".
        fields = dict(intent="preflight drift test", label="YouTube")
        first_ok = ([l.format(**fields) for l in fs.NAMED_INTENT_FIRST]
                    if fs.NAME_DISTRACTIONS else
                    [l.format(**fields) for l in fs.INTENT_FIRST_CALLOUT])
        if not msgs or msgs[0] not in first_ok:
            return "FAIL", f"first callout didn't name the intent and the distraction: {msgs}"

        session.set_nag_interval(fs.MIN_NAG_INTERVAL_SECONDS)
        time.sleep(fs.MIN_NAG_INTERVAL_SECONDS + 1.5)
        msgs2 = session.pop_announcements()
        tier2_ok = ([l.format(label="YouTube") for l in fs.NAMED_TIER_2]
                    if fs.NAME_DISTRACTIONS else fs.CALLOUT_TIER_2)
        if not msgs2 or msgs2[0] not in tier2_ok:
            return "FAIL", f"second nag was not a tier-2 line: {msgs2}"

        # Return to target and confirm it clears.
        current["host"] = "docs.example.com"
        time.sleep(2)
        if session.is_drifted:
            return "FAIL", "did not clear drift after returning to the locked target"

        return "PASS", "drift detected, escalated tier-1 -> tier-2, cleared on return"
    finally:
        fs.stop_session()


def check_focus_home_base_exemption():
    """The Jarvis tab itself must never register as a drift."""
    import focus_session as fs

    fs.stop_session()
    fake_tab = lambda: {"browser": "Chrome", "title": "x", "url": "https://docs.example.com/"}
    session = fs.start_session("preflight home base test", 5, get_tab_fn=fake_tab, get_window_fn=None)
    try:
        time.sleep(1.2)
        jarvis_tab = lambda: {"browser": "Chrome", "title": "Jarvis", "url": "http://localhost:4700/"}
        identity_hash, is_home = fs._reader_boundary(jarvis_tab, None)
        if not is_home:
            return "FAIL", "the Jarvis tab (localhost:4700) was not recognized as home base"
        session.record_tick(identity_hash, is_home)
        if session.is_drifted:
            return "FAIL", "visiting the Jarvis tab registered as a drift"
        return "PASS", None
    finally:
        fs.stop_session()


def check_focus_retarget():
    """
    Prompt 11 end to end through the real retarget() with controllable fake
    readers (this sandbox has no desktop to read): from a work tab it locks
    and forgives the drift; from the Jarvis tab it re-arms the deferred
    lock; from the desktop card it reads the browser tab, never the card.
    """
    import focus_session as fs

    surface = {"proc": "opera.exe", "title": "work", "url": "https://docs.example.com/"}
    get_tab = lambda: {"browser": "Opera GX", "title": "t", "url": surface["url"]}
    get_win = lambda: {"process": surface["proc"], "title": surface["title"], "pid": 1}

    fs.stop_session()
    session = fs.start_session("preflight retarget", 5, get_tab_fn=get_tab, get_window_fn=get_win)
    try:
        if session.deferred:
            return "FAIL", "started on a work tab but deferred anyway"

        surface["url"] = "https://youtube.com/"
        outcome, line = fs.retarget(get_tab, get_win)
        if outcome != "locked" or line != fs.LOCKED_ON_LINE:
            return "FAIL", f"work-tab re-target gave {outcome!r}: {line!r}"
        time.sleep(1.5)
        if session.is_drifted:
            return "FAIL", "still drifting on the tab it was just re-targeted to"

        surface["url"] = "http://localhost:4700/"
        outcome, line = fs.retarget(get_tab, get_win)
        if outcome != "rearmed" or not session.deferred:
            return "FAIL", f"Jarvis-tab re-target did not re-arm the deferred lock ({outcome!r})"

        surface.update(proc="python.exe", title="Jarvis Focus", url="https://docs.example.com/")
        outcome, _ = fs.retarget(get_tab, get_win, from_card=True)
        card_hash = fs._hash_identity("python.exe", None)
        if outcome != "locked" or session.target_hash == card_hash:
            return "FAIL", "card re-target locked the card itself (the card trap)"
        if session.target_hash != fs._hash_identity("Opera GX", "docs.example.com"):
            return "FAIL", "card re-target did not lock the browser's front tab"

        return "PASS", "work tab locks, Jarvis tab re-arms, card reads the browser (not itself)"
    finally:
        fs.stop_session()


def check_focus_distraction_privacy():
    """
    Prompt 12's privacy law, against the real watcher thread: a made-up
    site name nobody would ever write drives a spoken line, and is then
    absent from the session's state, the status the browser gets, the
    report, the ledger on disk and the notes folder. (A check that grepped
    for "Instagram" would be fooled by the example lines in the code.)
    """
    import focus_session as fs

    fake = "prflt-oddsite-4419.test"
    where = {"url": "https://docs.example.com/"}
    get_tab = lambda: {"browser": "Opera GX", "title": "t", "url": where["url"]}
    get_win = lambda: {"process": "opera.exe", "title": "w", "pid": 1, "display_name": "Opera"}

    fs.stop_session()
    session = fs.start_session(None, 5, get_tab_fn=get_tab, get_window_fn=get_win)
    try:
        time.sleep(1.2)
        where["url"] = f"https://{fake}/x"
        time.sleep(fs.DRIFT_GRACE_SECONDS + 1.6)
        spoken = session.pop_announcements()
        if fs.NAME_DISTRACTIONS and not any(fake in m for m in spoken):
            return "FAIL", f"the made-up site never reached the spoken line: {spoken}"
        state = "\n".join(f"{k}={v!r}" for k, v in vars(session).items() if k != "_lock")
        if fake in state:
            return "FAIL", "the site name was kept in the session's state"
        if fake in json.dumps(session.status_dict()) or fake in json.dumps(session.report()):
            return "FAIL", "the site name leaked into the status or the report"
    finally:
        fs.stop_session()
    ledger_after = open(fs.LEDGER_PATH).read() if os.path.exists(fs.LEDGER_PATH) else ""
    if fake in ledger_after:
        return "FAIL", "the site name was written to the ledger"
    notes_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notes")
    for root, _d, files in os.walk(notes_dir):
        for f in files:
            with open(os.path.join(root, f), encoding="utf-8", errors="ignore") as fh:
                if fake in fh.read():
                    return "FAIL", f"the site name appears in a note: {f}"
    return "PASS", "a made-up site was named out loud, then found nowhere: state, status, report, ledger, notes"


def check_focus_ledger_whitelist():
    """The ledger's key whitelist is enforced at write time, not just by
    convention \u2014 confirms the real file on disk only ever gets the
    whitelisted aggregate fields."""
    import focus_session as fs

    if os.path.exists(fs.LEDGER_PATH):
        os.remove(fs.LEDGER_PATH)
    try:
        bad_entry = {"timestamp": time.time(), "planned_minutes": 5, "app_name": "Chrome"}
        try:
            fs._write_ledger_entry(bad_entry)
            return "FAIL", "ledger accepted a non-whitelisted key (app_name) without error"
        except ValueError:
            pass

        good_entry = {
            "timestamp": time.time(), "planned_minutes": 5, "active_minutes": 5,
            "on_target_minutes": 5, "drifts": 0, "seconds_adrift": 0,
            "percent": 100, "completed": True,
        }
        fs._write_ledger_entry(good_entry)
        with open(fs.LEDGER_PATH) as f:
            contents = f.read()
        if "app_name" in contents or "Chrome" in contents:
            return "FAIL", "rejected entry's data still made it onto disk somehow"
        return "PASS", "ledger enforces its whitelist; no identity data on disk"
    finally:
        if os.path.exists(fs.LEDGER_PATH):
            os.remove(fs.LEDGER_PATH)


def _snapshot_ledger():
    """The user's real focus history (streak included) lives in
    focus_ledger.json. Several focus checks below start and stop throwaway
    sessions, and the whitelist check deletes the file outright -- so
    before 2.2.0, every preflight run wiped the user's history. Now the
    exact bytes are saved first and put back afterwards, whatever happens."""
    import focus_session as fs
    if os.path.exists(fs.LEDGER_PATH):
        with open(fs.LEDGER_PATH, "rb") as f:
            return fs.LEDGER_PATH, f.read()
    return fs.LEDGER_PATH, None


def _restore_ledger(snapshot):
    path, data = snapshot
    if data is None:
        if os.path.exists(path):
            os.remove(path)
    else:
        with open(path, "wb") as f:
            f.write(data)


def check_focus_diag():
    """Prompt 16: the RUNNING server's focus diag -- flags/counts/status only."""
    status, body = http_get("/focus/diag")
    if status != 200:
        return "FAIL", f"/focus/diag answered HTTP {status}"
    d = json.loads(body)
    tab_states = {"read", "unreadable", "not_a_browser", "tab_only", "no_window_reader"}
    bad = [k for k, v in d.items() if not (isinstance(v, (bool, int)) or (k == "tab_read" and v in tab_states))]
    if bad:
        return "FAIL", f"/focus/diag carries something other than a flag: {bad}"
    if not d["frontmost_readable"]:
        return "WARN", f"frontmost app not readable from here (tab read: {d['tab_read']})"
    return "PASS", (f"front readable, browser={d['frontmost_is_browser']}, tab read={d['tab_read']}, "
                    f"session on={d['session_on']}")


def check_patterns():
    """Phase 7: patterns come with evidence, or an honest 'not enough yet'."""
    status, body = http_get("/patterns")
    if status != 200:
        return "FAIL", f"/patterns answered HTTP {status}"
    d = json.loads(body)
    if any(not p.get("evidence") for p in d["patterns"]):
        return "FAIL", "a pattern came without its evidence"
    rd = d["readiness"]
    if not d["ready"]:
        return "PASS", f"not enough history yet ({rd['notes']} notes over {rd['span_days']} days), and it says so"
    return "PASS", f"{len(d['patterns'])} pattern(s), each with evidence"


def main():
    print(f"Running preflight checks against {BASE_URL}\n")
    snapshot = _snapshot_ledger()
    try:
        _run_checks()
    finally:
        _restore_ledger(snapshot)
        print("(your focus ledger was restored exactly as it was before preflight ran)")

    passed = sum(1 for _, s, _ in results if s == "PASS")
    failed = sum(1 for _, s, _ in results if s == "FAIL")
    warned = sum(1 for _, s, _ in results if s == "WARN")
    print(f"\n{passed} pass, {failed} fail, {warned} warn")
    sys.exit(1 if failed else 0)


def _run_checks():
    check("Server is up and serving the viewer", check_server_up)
    check("Galaxy matches the notes folder", check_graph_loads)
    check("Served files match what's on disk", check_files_served_match_disk)
    check("config.json is NOT reachable from the browser", check_config_not_browser_reachable)
    check("API key in config.json is valid", check_api_key_valid)
    check("Models in model_chain are reachable", check_model_chain_reachable)
    check("/remember writes and is immediately searchable", check_remember_round_trip)
    check("Capture mode keeps your exact words, into the INBOX", check_capture_mode_round_trip)
    check("Paper: photo -> read -> save -> photo kept", check_paper_round_trip)
    check("Sorting: a real reading, stored beside the note; a wish is not a task", check_sorting_round_trip)
    check("Reviews build from your notes; scheduler running", check_reviews)
    check("Every note has one record; a sync changes no note", check_records_integrity)
    check("/diag reports records + usage tracker health", check_diag_endpoint)
    check("Usage data holds names only, no page addresses", check_usage_store_names_only)
    check("/see answers a real JPEG", check_see_endpoint)
    check("Focus session start/pause/resume/stop round trip", check_focus_session_lifecycle)
    check("Focus session status is privacy-safe over real HTTP", check_focus_privacy_over_http)
    check("Focus session drift detection + tier escalation (takes ~40s)", check_focus_drift_and_escalation)
    check("Focus session Jarvis-tab home-base exemption", check_focus_home_base_exemption)
    check("Focus re-target: work tab / Jarvis tab / desktop card", check_focus_retarget)
    check("Focus distraction naming stores the name nowhere", check_focus_distraction_privacy)
    check("Focus session ledger enforces its key whitelist", check_focus_ledger_whitelist)
    check("/focus/diag: flags only, from the running server", check_focus_diag)
    check("Patterns: evidence, or an honest 'not enough yet'", check_patterns)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print(f"\n{e}")
        sys.exit(1)
