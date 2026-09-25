"""
knowledge.py — 4.5.0 (Jarvis 5.0, Phase 1): what Jarvis knows ABOUT each note.

Nothing new is stored as a separate database. A "node" is worked out from
what's already there: the note's record (records.py), Jarvis's latest
reading of it (sorting.py), its loop and project (loops.py) and its links.
The few things only YOU can say are kept in the record under "meta":

    meta.type          what kind of information it is (you set it)
    meta.state         e.g. current / historical / superseded, or active /
                       reversed for a decision (you set it)
    meta.importance    1-5 (you set it)
    meta.supersedes    [ids]  this note replaces those
    meta.superseded_by id     a newer note replaces this one

So a note stays a plain, human-readable note, while Jarvis knows what kind of
thing it holds. Nothing is converted or rewritten (spec §4, §8).

SOURCE AND CONFIDENCE (spec §5) -- every node says where it came from:
    user_stated     you typed or said it       -> certain
    user_recorded   a file you put in notes/   -> certain
    ai_inferred     Jarvis's reading (type, category, dates...) -> a
                    confidence word from the model's number, and it stays
                    "needs confirmation" until you confirm it. An AI reading
                    never becomes a fact on its own.
Confidence words: certain / likely / uncertain / needs_confirmation.

TIME (spec §6) -- states per type:
    preference            current / historical / superseded / uncertain
    decision              active / completed / reversed / abandoned
    project               active / paused / completed / abandoned
    task/commitment/waiting  open / done / dropped   (the loop's state)
    everything else       current / historical / superseded
"Replaced by a newer note" marks the old one superseded (a decision:
reversed) and keeps both, so "what did I previously think?" still works.

last_referenced lives in notes/.jarvis/referenced.json (not in the records,
so answering a question doesn't rewrite every record it used).

Standard library only.
"""

import datetime
import json
import os
import threading

import records

TYPES = ("information", "observation", "decision", "commitment", "task", "waiting", "project",
         "idea", "reference", "preference")
TYPE_WORDS = {t: t.capitalize() for t in TYPES}
TYPE_WORDS["category"] = "Category"
ORIGINS = ("user_stated", "user_recorded", "ai_summarised", "ai_inferred", "ai_suggested", "ai_linked")
CONFIDENCE = ("certain", "likely", "uncertain", "needs_confirmation")
STATES = {
    "preference": ("current", "historical", "superseded", "uncertain"),
    "decision": ("active", "completed", "reversed", "abandoned"),
    "project": ("active", "paused", "completed", "abandoned"),
    "task": ("open", "done", "dropped"),
    "commitment": ("open", "done", "dropped"),
    "waiting": ("open", "done", "dropped"),
}
DEFAULT_STATES = ("current", "historical", "superseded")
LOOP_TYPES = ("task", "commitment", "waiting")
USER_SOURCES = ("typed", "spoken", "paper", "review", "person-note", "unknown")
HISTORICAL_AFTER_DAYS = 30        # a "for now" (temporary) note older than this reads as historical
REF_FILE = "referenced.json"
_REF_LOCK = threading.Lock()


# ---- small helpers ---------------------------------------------------------

def reading(sc):
    for it in reversed(sc.get("interpretations", [])):
        if it.get("kind") == "sorting":
            return it
    return None


def confidence_word(number, confirmed=False):
    if confirmed:
        return "certain"
    if number is None:
        return "needs_confirmation"
    if number >= 0.8:
        return "likely"
    if number >= 0.5:
        return "uncertain"
    return "needs_confirmation"


def _days_since(iso, today):
    try:
        return (today - datetime.date.fromisoformat(str(iso)[:10])).days
    except ValueError:
        return None


def _title(sc, notes_dir):
    try:
        return records.title_of(sc["path"], records._read_text(records.abs_path(sc["path"], notes_dir)))
    except (OSError, KeyError):
        return sc.get("path", "?")


# ---- type ------------------------------------------------------------------

def derived_type(sc):
    """What Jarvis reads the note as, from its reading/loop/project. None if
    there's nothing to go on yet (unsorted)."""
    pm = sc.get("project_meta")
    if pm:
        return "category" if pm.get("kind") == "category" else "project"
    r = reading(sc)
    if not r:
        return None
    v = r.get("value", {})
    kinds, memory = set(v.get("kinds", [])), v.get("memory")
    loop_kind = (sc.get("loop") or {}).get("kind")
    if kinds & {"decision", "decision_to_confirm"} or memory == "decision":
        return "decision"
    if "waiting_for" in kinds:
        return "waiting"
    if "commitment" in kinds:
        return "commitment"
    if kinds & {"task", "reminder", "purchase"} or loop_kind in ("to do", "to buy"):
        return "task"
    if memory == "preference":
        return "preference"
    if kinds & {"idea", "wonder", "future_possibility", "goal"} or memory in ("goal", "interest"):
        return "idea"
    if "knowledge" in kinds:
        return "reference"
    if kinds & {"reflection", "experience", "problem", "risk"} or memory in ("reflection", "current_state", "event"):
        return "observation"
    return "information"


def node_type(sc):
    """(type, set_by): yours if you set one, else Jarvis's reading."""
    t = (sc.get("meta") or {}).get("type")
    if t in TYPES:
        return t, "you"
    d = derived_type(sc)
    return (d, "jarvis") if d else ("information", "none")


# ---- origin and confidence --------------------------------------------------

def origin(sc):
    """Where the note itself came from (spec §5)."""
    src = sc.get("source")
    if src == "file":
        return "user_recorded"
    if src == "jarvis":
        return "ai_suggested"
    return "user_stated"


def classification(sc):
    """How sure Jarvis is about what it thinks this note IS."""
    if (sc.get("meta") or {}).get("type") in TYPES:
        return {"origin": "user_stated", "confidence": "certain", "confirmed": True, "model": None, "at": None}
    if sc.get("project_meta"):
        return {"origin": "user_stated", "confidence": "certain", "confirmed": True, "model": None, "at": None}
    r = reading(sc)
    if not r:
        return {"origin": None, "confidence": "needs_confirmation", "confirmed": False, "model": None, "at": None}
    confirmed = bool(r.get("confirmed"))
    return {"origin": "user_stated" if confirmed else "ai_inferred",
            "confidence": confidence_word(r.get("confidence"), confirmed),
            "confirmed": confirmed, "model": r.get("model"), "at": r.get("at")}


# ---- temporal state ------------------------------------------------------------

def states_for(t):
    return STATES.get(t, DEFAULT_STATES)


def node_state(sc, t, today=None):
    """(state, set_by)."""
    today = today or datetime.date.today()
    meta = sc.get("meta") or {}
    if t == "project":
        s = (sc.get("project_meta") or {}).get("status", "active")
        return ("completed" if s == "done" else s), "you"
    if t == "category":
        return "current", "none"
    if t in LOOP_TYPES and sc.get("loop"):
        return sc["loop"].get("state", "open"), ("you" if sc["loop"].get("by") == "you" else "jarvis")
    if meta.get("state") in states_for(t):
        return meta["state"], "you"
    if meta.get("superseded_by"):
        return ("reversed" if t == "decision" else "superseded"), "you"
    if t == "decision":
        return "active", "jarvis"
    if t in LOOP_TYPES:
        return "open", "jarvis"
    r = reading(sc)
    if r and r.get("value", {}).get("temporary"):
        age = _days_since(r.get("at") or sc.get("created"), today)
        if age is not None and age > HISTORICAL_AFTER_DAYS:
            return "historical", "jarvis"
    if t == "preference" and r and not r.get("confirmed") and (r.get("confidence") or 0) < 0.5:
        return "uncertain", "jarvis"
    return "current", "jarvis"


def is_current(n):
    """For retrieval: False for anything replaced, reversed, abandoned or
    historical (spec §21: current before historical)."""
    return n["state"] not in ("superseded", "historical", "reversed", "abandoned", "dropped")


# ---- last referenced -------------------------------------------------------------

def _ref_path(notes_dir=None):
    return os.path.join(records.store_dir(notes_dir), REF_FILE)


def load_referenced(notes_dir=None):
    try:
        with open(_ref_path(notes_dir), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def touch(ids, notes_dir=None):
    """Records that these notes were just used to answer something."""
    ids = [i for i in ids if i]
    if not ids:
        return
    with _REF_LOCK:
        d = load_referenced(notes_dir)
        now = records.now_iso()
        for i in ids:
            d[i] = now
        try:
            records._atomic_write_json(_ref_path(notes_dir), d)
        except OSError:
            pass


def touch_paths(paths, notes_dir=None):
    ids = []
    for p in paths:
        sc = records.find_by_path(p, notes_dir) if p else None
        if sc:
            ids.append(sc["id"])
    touch(ids, notes_dir)


# ---- importance -----------------------------------------------------------------

def importance(sc, t, state, today=None):
    """(1-5, set_by). Worked out unless you set it."""
    meta = sc.get("meta") or {}
    if isinstance(meta.get("importance"), int) and 1 <= meta["importance"] <= 5:
        return meta["importance"], "you"
    today = today or datetime.date.today()
    score = 1
    loop = sc.get("loop") or {}
    if loop.get("state") == "open":
        score += 1
        due = loop.get("due")
        if due:
            left = -(_days_since(due, today) or 0)
            if left <= 7:
                score += 1
    if t == "decision" and state == "active":
        score += 1
    if t == "project" and state == "active":
        score += 1
    if len(sc.get("links", [])) >= 3:
        score += 1
    return min(score, 5), "jarvis"


# ---- the node ------------------------------------------------------------------

def node(sc, recs=None, notes_dir=None, refs=None, today=None):
    """Everything Jarvis knows about one note, in one dict (spec §8)."""
    recs = recs if recs is not None else records.load_all(notes_dir)[0]
    refs = refs if refs is not None else load_referenced(notes_dir)
    today = today or datetime.date.today()
    t, t_by = node_type(sc)
    state, s_by = node_state(sc, t, today)
    imp, i_by = importance(sc, t, state, today)
    meta = sc.get("meta") or {}
    r = reading(sc)
    v = (r or {}).get("value", {})
    loop = sc.get("loop") or {}
    cat = recs.get(sc.get("filed_under") or "")
    project_id = loop.get("project") or (sc.get("filed_under") if cat and (cat.get("project_meta") or {}).get("kind") != "category" else None)
    # related: same project/category, or linked by title
    link_ids = {l.get("target_id") for l in sc.get("links", []) if l.get("target_id")}
    link_titles = {(l.get("target_title") or "").lower() for l in sc.get("links", []) if l.get("target_title")}
    related_decisions, related_tasks = [], []
    for oid, o in recs.items():
        if oid == sc["id"] or o.get("missing"):
            continue
        same_group = project_id and ((o.get("loop") or {}).get("project") == project_id or o.get("filed_under") == project_id)
        linked = oid in link_ids or (bool(link_titles) and _title(o, notes_dir).lower() in link_titles)
        if not (same_group or linked):
            continue
        ot, _ = node_type(o)
        if ot == "decision":
            related_decisions.append(oid)
        elif ot in LOOP_TYPES and (o.get("loop") or {}).get("state", "open") == "open":
            related_tasks.append(oid)
    hist = sc.get("history", [])
    cls = classification(sc)
    return {
        "id": sc["id"], "path": sc.get("path"), "title": _title(sc, notes_dir),
        "type": t, "type_word": TYPE_WORDS.get(t, t), "type_set_by": t_by,
        "state": state, "state_set_by": s_by, "states": list(states_for(t)),
        "importance": imp, "importance_set_by": i_by,
        "origin": origin(sc), "source": sc.get("source"),
        "classification": cls,
        "needs_confirmation": cls["confidence"] == "needs_confirmation" or (cls["origin"] == "ai_inferred" and not cls["confirmed"]),
        "created": sc.get("created"), "created_from": sc.get("created_from"),
        "updated": hist[-1].get("at") if hist else sc.get("created"),
        "last_referenced": refs.get(sc["id"]),
        "category": _title(cat, notes_dir) if cat else None,
        "project": project_id,
        "tags": sorted(set(v.get("themes", []) + v.get("people", []) + v.get("places", []) + v.get("organisations", []))),
        "links": [{"title": (_title(recs[l["target_id"]], notes_dir) if l.get("target_id") in recs else l.get("target_title")),
                   "id": l.get("target_id"), "by": l.get("by", "you"),
                   "origin": "ai_linked" if l.get("by") == "jarvis" else "user_stated",
                   "confirmed": l.get("confirmed", l.get("by") != "jarvis"), "reason": l.get("reason")}
                  for l in sc.get("links", [])],
        "supersedes": list(meta.get("supersedes", [])), "superseded_by": meta.get("superseded_by"),
        "related_decisions": related_decisions, "related_tasks": related_tasks,
        "loop": ({"kind": loop.get("kind"), "state": loop.get("state"), "due": loop.get("due")} if loop else None),
    }


def all_nodes(notes_dir=None, today=None):
    recs, _ = records.load_all(notes_dir)
    refs = load_referenced(notes_dir)
    return [node(sc, recs, notes_dir, refs, today) for sc in recs.values() if not sc.get("missing")]


# ---- changes you make (undoable, logged) -------------------------------------------

def set_meta(record_id, notes_dir=None, **changes):
    """type / state / importance / supersedes (another id: THIS note replaces
    that one) / clear (a field name to go back to Jarvis's reading).
    Returns (spoken, undo_token). Raises ValueError for anything invalid."""
    import actions
    import loops
    with records._LOCK:
        recs, _ = records.load_all(notes_dir)
        sc = recs.get(record_id)
        if not sc or sc.get("missing"):
            raise ValueError("I can't find that note")
        before = actions.begin(notes_dir)
        meta = sc.setdefault("meta", {})
        title = _title(sc, notes_dir)
        said = []
        if changes.get("clear") in ("type", "state", "importance"):
            meta.pop(changes["clear"], None)
            records._event(sc, f"{changes['clear']} back to Jarvis's reading", by="you")
            said.append(f"'{title}' is back to my reading for its {changes['clear']}")
        if "type" in changes:
            t = changes["type"]
            if t not in TYPES:
                raise ValueError("unknown type")
            meta["type"] = t
            records._event(sc, "type set", type=t, by="you")
            said.append(f"'{title}' is now a {TYPE_WORDS[t].lower()}")
        t, _ = node_type(sc)
        if "state" in changes:
            s = changes["state"]
            if s not in states_for(t):
                raise ValueError(f"a {TYPE_WORDS.get(t, t).lower()} can't be '{s}'")
            if t == "project":
                pm = sc["project_meta"]
                pm["status"] = "done" if s == "completed" else s
                records._event(sc, "project status", status=pm["status"], by="you")
            elif t in LOOP_TYPES and sc.get("loop"):
                records.save(sc, notes_dir)
                loops.update_loop(record_id, {"done": "done", "dropped": "drop", "open": "reopen"}[s], notes_dir=notes_dir)
                sc = records.load_all(notes_dir)[0][record_id]
                meta = sc.setdefault("meta", {})
            else:
                meta["state"] = s
                records._event(sc, "state set", state=s, by="you")
            said.append(f"marked {s}")
        if "importance" in changes:
            try:
                n = int(changes["importance"])
            except (TypeError, ValueError):
                raise ValueError("importance is 1 to 5")
            if not 1 <= n <= 5:
                raise ValueError("importance is 1 to 5")
            meta["importance"] = n
            records._event(sc, "importance set", importance=n, by="you")
            said.append(f"importance {n}")
        if changes.get("supersedes"):
            old = recs.get(changes["supersedes"])
            if not old or old.get("missing") or old["id"] == record_id:
                raise ValueError("I can't find the older note")
            meta.setdefault("supersedes", [])
            if old["id"] not in meta["supersedes"]:
                meta["supersedes"].append(old["id"])
            om = old.setdefault("meta", {})
            om["superseded_by"] = record_id
            om.pop("state", None)
            records._event(sc, "replaces an older note", older=old["id"], by="you")
            records._event(old, "replaced by a newer note", newer=record_id, by="you")
            records.save(old, notes_dir)
            ot, _ = node_type(old)
            said.append(f"it replaces '{_title(old, notes_dir)}', which is kept as "
                        + ("reversed" if ot == "decision" else "superseded"))
        if not said:
            raise ValueError("nothing to change")
        records.save(sc, notes_dir)
        token = actions.commit(before, notes_dir)
    return "Done: " + "; ".join(said) + ".", token


def type_counts(notes_dir=None):
    out = {}
    for n in all_nodes(notes_dir):
        out[n["type"]] = out.get(n["type"], 0) + 1
    return out
