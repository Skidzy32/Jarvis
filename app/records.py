"""
records.py — Personal OS Phase 1 (2.3.0): record integrity.

Your notes stay exactly what they've always been: plain markdown files in
notes/. Nothing here ever writes to, rewrites or deletes a note. What this
adds is a small record BESIDE each note, in notes/.jarvis/records/:

    - a stable id (survives the note being moved or renamed)
    - when it was created, and HOW we know that (the capture moment, a
      date written in the note, a date in the file name, or -- the
      weakest -- the file's last-modified time)
    - where it came from (typed, spoken, an existing file, a person note)
    - for captures: exactly what you said/typed, word for word, before
      any trigger phrase was stripped ("original_input")
    - a fingerprint of the note's text, so an edit made outside Jarvis is
      noticed and written into the history rather than silently absorbed
    - its home (INBOX for now; Phase 3 sorts), status, links, and a
      history of everything that happened to it
    - an empty list for AI interpretations. When Phase 3 adds them, each
      carries its confidence, which model, when, and whether you confirmed
      it -- always beside the note, never written over it.

Delete the whole notes/.jarvis folder and you lose only this metadata,
never a thought: the next start rebuilds a record for every note (with
the weaker "file" provenance). That's what makes it safe to evolve.

notes/.jarvis/index.sqlite is a search index built from the notes plus
these records. It's a cache: rebuilt whenever notes change, and it can
be deleted at any time.

Standard library only.
"""

import datetime
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading

SCHEMA = 1
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
NOTES_DIR = os.path.join(PROJECT_ROOT, "notes")
STORE_DIRNAME = ".jarvis"

# The spec's homes (section 4). Stored as a field, never as folders:
# moving files would break links and silently change your own filing.
HOMES = ("INBOX", "NOW", "PROJECTS", "AREAS", "KNOWLEDGE", "JOURNAL",
         "SOMEDAY", "ARCHIVE", "WONDER")
SOURCES = ("typed", "spoken", "unknown", "file", "person-note", "paper", "jarvis", "review")

# How a record's creation time is known, strongest first.
CREATED_FROM = ("capture-time", "date-in-note", "date-in-filename", "file-modified-time")

_LOCK = threading.RLock()

DATE_LINE_RE = re.compile(r"^(?:Captured|Added)\s+(\d{4}-\d{2}-\d{2})\b", re.MULTILINE)
DATE_IN_NAME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
TITLE_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


# ---- paths -----------------------------------------------------------------

def store_dir(notes_dir=None):
    return os.path.join(notes_dir or NOTES_DIR, STORE_DIRNAME)


def sidecar_dir(notes_dir=None):
    return os.path.join(store_dir(notes_dir), "records")


def index_path(notes_dir=None):
    return os.path.join(store_dir(notes_dir), "index.sqlite")


def rel_path(path, notes_dir=None):
    """A note's path relative to the notes folder, always with '/'."""
    root = os.path.abspath(notes_dir or NOTES_DIR)
    return os.path.relpath(os.path.abspath(path), root).replace(os.sep, "/")


def abs_path(rel, notes_dir=None):
    return os.path.join(notes_dir or NOTES_DIR, *rel.split("/"))


# ---- small helpers ---------------------------------------------------------

def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def new_id():
    return f"r-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_text(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def title_of(path, text=None):
    text = _read_text(path) if text is None else text
    m = TITLE_RE.search(text)
    if m and m.group(1).strip():
        return m.group(1).strip()
    name = os.path.splitext(os.path.basename(path))[0]
    return name.replace("-", " ").replace("_", " ").strip().title()


def _atomic_write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp-{secrets.token_hex(3)}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _event(sidecar, event, **detail):
    entry = {"at": now_iso(), "event": event}
    if detail:
        entry["detail"] = detail
    sidecar.setdefault("history", []).append(entry)


def infer_created(path):
    """(created, created_from) for a note that wasn't captured by Jarvis.
    Prefers a date the note itself states, then one in the file name, and
    only then the file's modified time -- and always says which it was."""
    try:
        text = _read_text(path)
    except OSError:
        text = ""
    m = DATE_LINE_RE.search(text)
    if m:
        return m.group(1), "date-in-note"
    m = DATE_IN_NAME_RE.search(os.path.basename(path))
    if m:
        return m.group(1), "date-in-filename"
    ts = datetime.datetime.fromtimestamp(os.path.getmtime(path)).astimezone()
    return ts.isoformat(timespec="seconds"), "file-modified-time"


# ---- sidecar storage -------------------------------------------------------

def _sidecar_path(record_id, notes_dir=None):
    return os.path.join(sidecar_dir(notes_dir), f"{record_id}.json")


def save(sidecar, notes_dir=None):
    _atomic_write_json(_sidecar_path(sidecar["id"], notes_dir), sidecar)


def load_all(notes_dir=None):
    """Every record, by id. A record file that can't be read is reported,
    never deleted or overwritten -- see diag()."""
    out, unreadable = {}, []
    d = sidecar_dir(notes_dir)
    if not os.path.isdir(d):
        return out, unreadable
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, fn), "r", encoding="utf-8") as f:
                sc = json.load(f)
            out[sc["id"]] = sc
        except (OSError, ValueError, KeyError):
            unreadable.append(fn)
    return out, unreadable


def find_by_path(path, notes_dir=None):
    rel = rel_path(path, notes_dir)
    records, _ = load_all(notes_dir)
    for sc in records.values():
        if sc.get("path") == rel and not sc.get("missing"):
            return sc
    return None


def _new_sidecar(path, source, created, created_from, original_input, notes_dir):
    sc = {
        "schema": SCHEMA,
        "id": new_id(),
        "path": rel_path(path, notes_dir),
        "created": created,
        "created_from": created_from,
        "source": source,
        "original_input": original_input,
        "content_sha256": sha256_file(path),
        "home": "INBOX",
        "status": "unprocessed",
        "interpretations": [],
        "links": [],
        "missing": False,
        "history": [],
    }
    return sc


def create_for_capture(path, original_input, source="unknown", notes_dir=None,
                       extra=None, extra_event=None):
    """Called right after /remember writes a note. original_input is the
    whole message exactly as it arrived, trigger phrase and all.
    2.4.0: `extra` adds fields (a paper note's photo and transcription);
    `extra_event` adds detail to the "captured" history entry."""
    if source not in SOURCES:
        source = "unknown"
    with _LOCK:
        sc = _new_sidecar(path, source, now_iso(), "capture-time", original_input, notes_dir)
        for k, v in (extra or {}).items():
            if k not in sc or k in ("interpretations",):
                sc[k] = v
        _event(sc, "captured", source=source, **(extra_event or {}))
        save(sc, notes_dir)
        return sc


def create_for_file(note_path, source="file", event="found", notes_dir=None, **detail):
    with _LOCK:
        created, created_from = (now_iso(), "capture-time") if source == "person-note" else infer_created(note_path)
        sc = _new_sidecar(note_path, source, created, created_from, None, notes_dir)
        _event(sc, event, **detail)
        save(sc, notes_dir)
        return sc


def record_append(path, what, notes_dir=None, **detail):
    """Jarvis appended to a note at your request (a 'See also' link). The
    note's original text above is untouched; the record keeps the new
    fingerprint so this isn't later mistaken for an outside edit."""
    with _LOCK:
        sc = find_by_path(path, notes_dir)
        if sc is None:
            return None
        old = sc.get("content_sha256")
        sc["content_sha256"] = sha256_file(path)
        _event(sc, "appended by Jarvis", what=what, before_sha256=old, **detail)
        save(sc, notes_dir)
        return sc


def record_link(path, target_title, by="user", notes_dir=None):
    with _LOCK:
        sc = find_by_path(path, notes_dir)
        if sc is None:
            return None
        sc.setdefault("links", []).append({
            "target_title": target_title, "kind": "see_also",
            "by": by, "confirmed": by == "user", "at": now_iso(),
        })
        save(sc, notes_dir)
        return sc


def record_move(old_path, new_path, why="", notes_dir=None):
    with _LOCK:
        sc = find_by_path(old_path, notes_dir)
        if sc is None:
            return None
        old_rel = sc["path"]
        sc["path"] = rel_path(new_path, notes_dir)
        _event(sc, "moved", from_path=old_rel, to_path=sc["path"], why=why)
        save(sc, notes_dir)
        return sc


def forget(record_id, notes_dir=None):
    """Only for preflight's own test note, which it deletes afterwards.
    Nothing in normal use deletes a record."""
    with _LOCK:
        p = _sidecar_path(record_id, notes_dir)
        if os.path.exists(p):
            os.remove(p)


# ---- sync: notes folder -> records (never writes a note) -------------------

def note_files(notes_dir=None):
    """Every .md under notes/, relative, skipping Jarvis's own folder."""
    root = notes_dir or NOTES_DIR
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != STORE_DIRNAME]
        for fn in filenames:
            if fn.lower().endswith(".md"):
                out.append(rel_path(os.path.join(dirpath, fn), root))
    return sorted(out)


def sync(notes_dir=None):
    """
    Brings the records in line with the notes folder. Reads notes; writes
    only records. Returns what it did:
      created  -- notes that had no record (e.g. existing notes, first run)
      moved    -- a record's note turned up under a new path (same text)
      edited   -- a note's text changed outside Jarvis (logged, kept)
      missing  -- a note is gone; its record is kept and marked, not deleted
      restored -- a missing note came back
    """
    report = {"created": [], "moved": [], "edited": [], "missing": [], "restored": []}
    with _LOCK:
        files = note_files(notes_dir)
        hashes = {}
        for rel in files:
            try:
                hashes[rel] = sha256_file(abs_path(rel, notes_dir))
            except OSError:
                pass
        records, _unreadable = load_all(notes_dir)

        claimed = set()
        lost = []
        for sc in records.values():
            rel = sc.get("path")
            if rel in hashes and rel not in claimed:
                claimed.add(rel)
                changed = False
                if sc.get("missing"):
                    sc["missing"] = False
                    _event(sc, "note found again", path=rel)
                    report["restored"].append(rel)
                    changed = True
                if sc.get("content_sha256") != hashes[rel]:
                    _event(sc, "edited outside Jarvis", before_sha256=sc.get("content_sha256"),
                           after_sha256=hashes[rel])
                    sc["content_sha256"] = hashes[rel]
                    report["edited"].append(rel)
                    changed = True
                if changed:
                    save(sc, notes_dir)
            else:
                lost.append(sc)

        unclaimed = [rel for rel in files if rel not in claimed and rel in hashes]
        for sc in lost:
            match = next((rel for rel in unclaimed if hashes[rel] == sc.get("content_sha256")), None)
            if match:
                unclaimed.remove(match)
                old = sc.get("path")
                sc["path"] = match
                was_missing = sc.get("missing")
                sc["missing"] = False
                _event(sc, "moved", from_path=old, to_path=match, why="found by matching text")
                save(sc, notes_dir)
                report["restored" if was_missing else "moved"].append(match)
            elif not sc.get("missing"):
                sc["missing"] = True
                _event(sc, "note missing", path=sc.get("path"))
                save(sc, notes_dir)
                report["missing"].append(sc.get("path"))

        for rel in unclaimed:
            create_for_file(abs_path(rel, notes_dir), source="file", event="found",
                            notes_dir=notes_dir, where=rel)
            report["created"].append(rel)
    return report


# ---- the index (a rebuildable cache) ---------------------------------------

def _fts_available():
    try:
        c = sqlite3.connect(":memory:")
        c.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        c.close()
        return True
    except sqlite3.Error:
        return False


def rebuild_index(notes_dir=None):
    """Rebuilds notes/.jarvis/index.sqlite from the notes + records, in one
    transaction (a reader sees the old index or the new one, never half)."""
    with _LOCK:
        records, _ = load_all(notes_dir)
        fts = _fts_available()
        os.makedirs(store_dir(notes_dir), exist_ok=True)
        con = sqlite3.connect(index_path(notes_dir))
        try:
            con.execute("BEGIN")
            con.execute("DROP TABLE IF EXISTS records")
            con.execute("DROP TABLE IF EXISTS body")
            con.execute("DROP TABLE IF EXISTS meta")
            con.execute("""CREATE TABLE records (
                id TEXT PRIMARY KEY, path TEXT, title TEXT, home TEXT, status TEXT,
                source TEXT, created TEXT, created_from TEXT, missing INTEGER,
                has_original_input INTEGER, links INTEGER, history INTEGER)""")
            if fts:
                con.execute("CREATE VIRTUAL TABLE body USING fts5(id UNINDEXED, title, text)")
            else:
                con.execute("CREATE TABLE body (id TEXT, title TEXT, text TEXT)")
            con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
            for sc in records.values():
                text, title = "", sc.get("path", "")
                if not sc.get("missing"):
                    try:
                        text = _read_text(abs_path(sc["path"], notes_dir))
                        title = title_of(sc["path"], text)
                    except OSError:
                        pass
                con.execute("INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                    sc["id"], sc.get("path"), title, sc.get("home"), sc.get("status"),
                    sc.get("source"), sc.get("created"), sc.get("created_from"),
                    int(bool(sc.get("missing"))), int(sc.get("original_input") is not None),
                    len(sc.get("links", [])), len(sc.get("history", [])),
                ))
                if not sc.get("missing"):
                    con.execute("INSERT INTO body VALUES (?,?,?)", (sc["id"], title, text))
            con.execute("INSERT INTO meta VALUES ('built_at', ?)", (now_iso(),))
            con.execute("INSERT INTO meta VALUES ('full_text_search', ?)", ("fts5" if fts else "basic",))
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()
        return len(records)


def search(query, limit=10, notes_dir=None):
    """Full-text search over the index: [(id, path, title)]. Used by /diag
    and preflight now; Phase 6's question-answering builds on it."""
    words = re.findall(r"[A-Za-z0-9]+", query or "")
    if not words or not os.path.exists(index_path(notes_dir)):
        return []
    con = sqlite3.connect(index_path(notes_dir))
    try:
        mode = con.execute("SELECT value FROM meta WHERE key='full_text_search'").fetchone()
        if mode and mode[0] == "fts5":
            q = " ".join(f'"{w}"' for w in words)
            rows = con.execute(
                "SELECT r.id, r.path, r.title FROM body b JOIN records r ON r.id = b.id "
                "WHERE body MATCH ? ORDER BY rank LIMIT ?", (q, limit)).fetchall()
        else:
            where = " AND ".join("(b.text LIKE ? OR b.title LIKE ?)" for _ in words)
            args = [a for w in words for a in (f"%{w}%", f"%{w}%")]
            rows = con.execute(
                f"SELECT r.id, r.path, r.title FROM body b JOIN records r ON r.id = b.id "
                f"WHERE {where} LIMIT ?", (*args, limit)).fetchall()
        return rows
    finally:
        con.close()


def sync_and_index(notes_dir=None):
    report = sync(notes_dir)
    rebuild_index(notes_dir)
    return report


# ---- health ----------------------------------------------------------------

def diag(notes_dir=None):
    """The truth about the record store, for /diag (Prompt 16's law:
    build the truth endpoint before you need it)."""
    files = note_files(notes_dir)
    records, unreadable = load_all(notes_dir)
    live = [sc for sc in records.values() if not sc.get("missing")]
    by_path = {}
    for sc in live:
        by_path.setdefault(sc.get("path"), []).append(sc["id"])
    info = {
        "notes": len(files),
        "records": len(records),
        "notes_without_record": sorted(set(files) - set(by_path)),
        "records_for_missing_notes": sorted(sc.get("path") for sc in records.values() if sc.get("missing")),
        "notes_with_two_records": sorted(p for p, ids in by_path.items() if len(ids) > 1),
        "unreadable_record_files": unreadable,
        "homes": {},
        "created_from": {},
        "captures_with_original_input": sum(1 for sc in live if sc.get("original_input") is not None),
        "index": None,
    }
    for sc in live:
        info["homes"][sc.get("home")] = info["homes"].get(sc.get("home"), 0) + 1
        info["created_from"][sc.get("created_from")] = info["created_from"].get(sc.get("created_from"), 0) + 1
    ip = index_path(notes_dir)
    if os.path.exists(ip):
        con = sqlite3.connect(ip)
        try:
            meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
            n = con.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            info["index"] = {"built_at": meta.get("built_at"), "search": meta.get("full_text_search"),
                             "records": n, "matches_records": n == len(records)}
        except sqlite3.Error as e:
            info["index"] = {"error": str(e)}
        finally:
            con.close()
    info["healthy"] = (not info["notes_without_record"] and not info["notes_with_two_records"]
                       and not unreadable and bool(info["index"]) and info["index"].get("matches_records", False))
    return info


if __name__ == "__main__":
    import sys
    if "--rebuild" in sys.argv:
        print(json.dumps(sync_and_index(), indent=2))
    print(json.dumps(diag(), indent=2))
