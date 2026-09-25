"""
maintenance.py — Personal OS Phase 9 (3.0.0): Jarvis keeps things tidy
so you don't have to (spec §22).

Already done elsewhere, and left as it is: related notes are linked when
sorting (2.5.0); stale projects, open promises, forgotten to-dos,
deadlines, waiting-fors and repeated themes are raised by the reviews and
"anything forgotten?" (2.7.0); the search index is rebuilt on every change
(2.3.0).

New here:
  - AUTO-SORT: once a day, if anything is waiting in the inbox and there's
    a key, Jarvis sorts ONE batch (up to 8 notes, 1 request of OpenRouter's
    free daily 50). "auto_sort": false in review_settings.json turns it off.
  - DUPLICATES: two notes saying the same thing -> "same thing?" Merging
    never deletes: the newer note is filed in Archive, marked as a
    duplicate of the older one, and linked to it.
  - SUGGEST ARCHIVE: finished loops, done projects, and "for now" notes
    over a month old.
  - WORTH A SECOND LOOK: readings you never confirmed, where the model
    wasn't very sure, after a week.
  - UNCONNECTED: how many notes aren't linked to anything (information only).
Anything you wave away ("keep") isn't suggested again.

Suggestions only change where a note is FILED (its record) -- never the note.
Standard library only.
"""

import datetime
import json
import os
import re

import records
import reviews
import sorting

DUP_SIMILARITY = 0.8
ARCHIVE_TEMPORARY_DAYS = 30
ARCHIVE_DONE_DAYS = 14
SECOND_LOOK_DAYS = 7
SECOND_LOOK_BELOW = 0.7
STOP = {"the", "a", "an", "to", "of", "and", "or", "for", "on", "in", "at", "is", "it", "my", "i", "me",
        "that", "this", "with", "be", "about", "remember", "need", "note"}


def _state_path(notes_dir=None):
    return os.path.join(records.store_dir(notes_dir), "maintenance_state.json")


def load_state(notes_dir=None):
    try:
        with open(_state_path(notes_dir), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"dismissed": [], "last_auto_sort": None, "last_report": None}


def save_state(state, notes_dir=None):
    records._atomic_write_json(_state_path(notes_dir), state)


def _words(text):
    return {w for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in STOP}


def _similar(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def report(notes_dir=None, today=None):
    today = today or datetime.date.today()
    state = load_state(notes_dir)
    dismissed = set(state.get("dismissed", []))
    recs, _ = records.load_all(notes_dir)
    live = [sc for sc in recs.values() if not sc.get("missing") and sc.get("home") != "ARCHIVE"]
    texts = {}
    for sc in live:
        try:
            # the note's own words only (not Jarvis's title/date lines)
            texts[sc["id"]] = _words(sorting.note_words(sc["path"], notes_dir))
        except OSError:
            continue
    out = {"duplicates": [], "archive": [], "second_look": [], "unconnected": 0}

    # duplicates: very similar wording (the older one is kept)
    ordered = sorted(live, key=lambda s: s.get("created") or "")
    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            key = f"dup:{a['id']}:{b['id']}"
            if key in dismissed or a["id"] not in texts or b["id"] not in texts:
                continue
            if a.get("project_meta") or b.get("project_meta") or a.get("source") == "review" or b.get("source") == "review":
                continue
            if _similar(texts[a["id"]], texts[b["id"]]) >= DUP_SIMILARITY:
                out["duplicates"].append({"key": key, "keep": a["id"], "dup": b["id"],
                                          "keep_title": reviews._title(a, notes_dir),
                                          "dup_title": reviews._title(b, notes_dir)})

    for sc in live:
        v = reviews._reading(sc)
        loop = sc.get("loop") or {}
        title = reviews._title(sc, notes_dir)
        age = reviews._days_since(sc.get("created"), today)
        reason = None
        if loop.get("state") in ("done", "dropped") and \
                (reviews._days_since(loop.get("closed_at"), today) or 0) >= ARCHIVE_DONE_DAYS:
            reason = f"{'done' if loop['state'] == 'done' else 'dropped'} {reviews._days_since(loop['closed_at'], today)} days ago"
        elif (sc.get("project_meta") or {}).get("status") in ("done", "abandoned"):
            reason = "project marked done"
        elif v.get("temporary") and age is not None and age >= ARCHIVE_TEMPORARY_DAYS and not loop.get("state") == "open":
            reason = f"described as a passing thing, {age} days ago"
        if reason and f"archive:{sc['id']}" not in dismissed:
            out["archive"].append({"key": f"archive:{sc['id']}", "id": sc["id"], "title": title, "reason": reason})

        r = next((i for i in reversed(sc.get("interpretations", [])) if i.get("kind") == "sorting"), None)
        if r and not r.get("confirmed") and sc.get("status") == "sorted" and \
                (r.get("confidence") or 0) < SECOND_LOOK_BELOW and \
                (reviews._days_since(r.get("at"), today) or 0) >= SECOND_LOOK_DAYS and \
                f"look:{sc['id']}" not in dismissed:
            out["second_look"].append({"key": f"look:{sc['id']}", "id": sc["id"], "title": title,
                                       "home": sc.get("home"), "confidence": r.get("confidence")})

        if not sc.get("links") and not any(v.get(f) for f in ("people", "places", "organisations")) \
                and not sc.get("project_meta") and not loop.get("project"):
            out["unconnected"] += 1
    out["spoken"] = spoken(out)
    return out


def spoken(r):
    bits = []
    if r["duplicates"]:
        bits.append(f"{len(r['duplicates'])} possible duplicate{'s' if len(r['duplicates']) != 1 else ''}")
    if r["archive"]:
        bits.append(f"{len(r['archive'])} thing{'s' if len(r['archive']) != 1 else ''} that could be archived")
    if r["second_look"]:
        bits.append(f"{len(r['second_look'])} I'm not sure I filed right")
    if not bits:
        return "Nothing needs tidying, sir."
    return "A little tidying, when you have a moment: " + ", ".join(bits) + ". It's in the inbox panel."


def apply(action, key=None, notes_dir=None, keep=None, dup=None, record_id=None):
    """archive / merge / keep (dismiss). Never edits or deletes a note."""
    state = load_state(notes_dir)
    with records._LOCK:
        recs, _ = records.load_all(notes_dir)
        if action == "keep":
            if not key:
                raise ValueError("nothing to keep")
            state.setdefault("dismissed", []).append(key)
            save_state(state, notes_dir)
            return "Left as it is, sir. I won't suggest it again."
        if action == "archive":
            sc = recs.get(record_id)
            if not sc:
                raise ValueError("that note isn't there any more")
            sc["home"], sc["status"] = "ARCHIVE", "sorted"
            records._event(sc, "archived by you (tidy-up suggestion)")
            records.save(sc, notes_dir)
            return "Archived, sir. Still there if you ever want it."
        if action == "merge":
            a, b = recs.get(keep), recs.get(dup)
            if not a or not b:
                raise ValueError("one of those notes isn't there any more")
            b["home"], b["status"], b["duplicate_of"] = "ARCHIVE", "sorted", a["id"]
            if b.get("loop") and b["loop"].get("state") == "open":
                b["loop"]["state"], b["loop"]["closed_at"] = "dropped", records.now_iso()
            for x, y in ((a, b), (b, a)):
                x.setdefault("links", []).append({"kind": "related", "target_id": y["id"], "target_path": y["path"],
                                                  "by": "you", "confirmed": True, "reason": "same thing",
                                                  "at": records.now_iso()})
            records._event(b, "marked as a duplicate by you", of=a["path"])
            records._event(a, "a duplicate was merged into this note's record", duplicate=b["path"])
            records.save(a, notes_dir)
            records.save(b, notes_dir)
            return "Merged, sir. The newer one is in Archive, linked to the original. Neither was deleted."
    raise ValueError("unknown action")


def auto_tick(now, call_brain, load_config, placeholder, notes_dir=None, settings=None):
    """Once a day: rebuild the index, and sort one batch if anything's
    waiting. Returns what it did (or None)."""
    settings = settings or {}
    state = load_state(notes_dir)
    today = now.date().isoformat()
    if state.get("last_auto_sort") == today or now.hour < 7:
        return None
    state["last_auto_sort"] = today           # once a day, whatever happens
    save_state(state, notes_dir)
    records.sync_and_index(notes_dir)

    def done(result):       # every day's outcome is kept for /diag, including "didn't sort"
        st = load_state(notes_dir)
        st["last_report"] = dict(result, at=now.isoformat(timespec="seconds"))
        save_state(st, notes_dir)
        return result
    if not settings.get("auto_sort", True):
        return done({"sorted": 0, "note": "auto_sort is off"})
    if not sorting.waiting(notes_dir):
        return done({"sorted": 0, "note": "nothing waiting"})
    try:
        config = load_config()
    except (OSError, ValueError):
        return done({"sorted": 0, "error": "config.json unreadable"})
    if config.get("openrouter_api_key", "") in ("", placeholder):
        return done({"sorted": 0, "error": "no key"})
    s = sorting.sort_inbox(call_brain, config, notes_dir=notes_dir, max_batches=1)
    return done({"sorted": len(s["sorted"]), "asks": len(s["needs_you"]), "error": s["error"]})
