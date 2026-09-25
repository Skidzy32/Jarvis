"""
activity.py — 4.5.0 (Jarvis 5.0 spec §18): one audit trail for everything.

Every note already keeps its own history (records.py). This joins them into
one append-only list, notes/.jarvis/activity.jsonl, so you can see what
happened across all of Jarvis and who did it:

    {"at", "event", "id", "note", "origin", "confidence", "approved", "detail"}

  origin      user            you did it (typed, spoken, clicked)
              ai_inferred     Jarvis's reading of a note (sorting)
              ai_linked       Jarvis linked two notes
              ai_suggested    Jarvis suggested something (not yet accepted)
              jarvis          Jarvis's own housekeeping (found a file, an index)
  approved    True when you confirmed it, False when you turned it down,
              None when nobody was asked

Nothing is ever rewritten or removed from the file. Logging can never stop a
save: any error here is swallowed. Standard library only.
"""

import json
import os
import threading

import records

FILE = "activity.jsonl"
_LOCK = threading.Lock()


def path(notes_dir=None):
    return os.path.join(records.store_dir(notes_dir), FILE)


def _origin(event, detail):
    d = detail or {}
    if d.get("model"):
        return "ai_inferred"
    by = d.get("by")
    if by == "jarvis":
        return "ai_linked" if "link" in event else "ai_suggested"
    if by in ("you", "user"):
        return "user"
    if event in ("found", "created from file", "indexed"):
        return "jarvis"
    return "user"


def log(event, notes_dir=None, record_id=None, note=None, origin=None, confidence=None,
        approved=None, at=None, **detail):
    entry = {"at": at or records.now_iso(), "event": event, "id": record_id, "note": note,
             "origin": origin or _origin(event, detail), "confidence": confidence, "approved": approved}
    if detail:
        entry["detail"] = detail
    try:
        p = path(notes_dir)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with _LOCK, open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return entry


def from_history(sc, entries, notes_dir=None):
    """Called by records.save for the history entries added since the last save."""
    reserved = {"notes_dir", "record_id", "note", "origin", "confidence", "approved", "at", "event"}
    for h in entries:
        d = dict(h.get("detail") or {})
        conf = d.get("confidence")
        extra = {k: v for k, v in d.items() if k not in reserved}
        log(h.get("event", "?"), notes_dir, record_id=sc.get("id"), note=sc.get("path"),
            origin=_origin(h.get("event", ""), d), confidence=conf,
            approved=True if d.get("confirmed") else None, at=h.get("at"), **extra)


def read(notes_dir=None, limit=100, record_id=None):
    """Newest first."""
    try:
        with open(path(notes_dir), encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if record_id and e.get("id") != record_id:
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out
