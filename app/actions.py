"""
actions.py — 3.7.0: do what you ask with your notes, in your own words.

"I said I need a new mic -- link that in with the two existing notes and
file all 3 as Jarvis Maintenance/Improvements." Linking and filing are core
jobs, and a fixed phrase ("link A and B") can't understand "that" or "the
two existing notes". So:

  1. The model only READS: it gets your request, the last few chat lines
     (so "that" means something), and a numbered list of your notes. It
     answers with a small plan in JSON -- which note numbers, which of four
     actions. It cannot write anything itself.
  2. Jarvis CHECKS the plan: every note number must exist; a new filing
     name must come from your own words (typos allowed, inventions not);
     at most 12 notes; only link / unlink / file / move.
  3. Jarvis DOES it, says exactly what it did in its own words (not the
     model's), and keeps an UNDO: one click puts every record back as it
     was, and removes a collection it created for this.

Filing: a collection is a project note (notes/projects/<name>.md), the same
thing "create a project" makes, so it shows in LOOPS and the overview. A
filed note gets a line to the collection's star in the galaxy, and a to-do
inside it also counts towards the project.

Notes are never edited; only Jarvis's records beside them change.
"""

import copy
import difflib
import itertools
import json
import os
import re
import threading
import time
import uuid

import links
import loops
import records
import reviews
import sorting

ACTIONS = ("remember", "link", "unlink", "file", "move")
MAX_NOTES_PER_ACTION = 12
CATALOGUE_MAX = 60
HISTORY_LINES = 6
UNDO_KEEP = 10

# Plain words that suggest you want something DONE to notes (not a question).
ACTION_HINT_RE = re.compile(
    r"\b(link|connect|join|tie|attach|file|put|move|group|organi[sz]e|categori[sz]e|tag|collect|bundle|"
    r"add\s+(?:it|that|this|them|these|those)\s+to|unlink|disconnect|separate)\b", re.I)

PLAN_SYSTEM_PROMPT = """You turn a request about the user's notes into a plan for Jarvis to carry out.
You do NOT carry anything out and you do NOT chat.

You get: the recent conversation (so "that", "it", "the last one" make sense), the request,
and a numbered list of the user's notes (newest first; "just now" marks notes captured in the
last few minutes). Existing collections are listed too.

Reply with ONLY this JSON:
{"actions": [ ... ], "question": null, "chat": false}

If the request is NOT asking to change the notes (it's a question, or just conversation), reply
{"actions": [], "question": null, "chat": true}.

Each action is one of:
  {"do": "remember", "text": "..."}                    save something the user SAID in the
                                                       conversation that isn't a note yet;
                                                       use their words. It becomes note number 0.
  {"do": "link",   "notes": [numbers]}                 connect these notes to each other
  {"do": "unlink", "notes": [numbers]}                 remove the connection between them
  {"do": "file",   "notes": [numbers], "collection": "name"}   file them under a collection
  {"do": "move",   "notes": [numbers], "home": "NOW|PROJECTS|AREAS|KNOWLEDGE|JOURNAL|SOMEDAY|ARCHIVE|WONDER"}

Rules:
- Use the note NUMBERS from the list. Never invent a note.
- "that" / "it" / "the one I just said" means the note marked "just discussed" if there is one,
  otherwise the newest note marked "just now". If what
  they mean was only said in the conversation and isn't in the list, "remember" it first and use 0.
- For a collection, use the user's own name for it, spelled properly. If an existing collection
  clearly matches what they said, use its exact name.
- If you honestly can't tell which notes they mean, return no actions and ask ONE short question
  in "question", naming the likely candidates.
"""


def _fuzzy_words_in(name, text):
    """Every word of `name` is close to some word the user actually said
    (typos allowed: 'Maintainance' ~ 'Maintenance')."""
    said = re.findall(r"[a-z0-9]+", text.lower())
    for w in re.findall(r"[a-z0-9]+", name.lower()):
        if w in links.STOP or len(w) <= 2:
            continue
        if not said or max(difflib.SequenceMatcher(None, w, s).ratio() for s in said) < 0.75:
            return False
    return True


def _collections(notes_dir):
    recs, _ = records.load_all(notes_dir)
    return [{"id": sc["id"], "name": reviews._title(sc, notes_dir), "path": sc["path"]}
            for sc in recs.values() if sc.get("project_meta") and not sc.get("missing")]


def catalogue(notes_dir, now=None, focus_id=None):
    now = now or time.time()
    recs, _ = records.load_all(notes_dir)
    live = [sc for sc in recs.values() if not sc.get("missing") and not sc.get("project_meta")]
    live.sort(key=lambda s: s.get("created") or "", reverse=True)
    cols = {c["id"]: c["name"] for c in _collections(notes_dir)}
    out = []
    for i, sc in enumerate(live[:CATALOGUE_MAX], 1):
        created = sc.get("created") or ""
        try:
            import datetime
            age = now - datetime.datetime.fromisoformat(created).timestamp()
        except ValueError:
            age = None
        out.append({"n": i, "id": sc["id"], "title": reviews._title(sc, notes_dir),
                    "created": created[:16], "just_now": age is not None and age < 15 * 60,
                    "discussed": sc["id"] == focus_id,
                    "home": sc.get("home"), "filed_in": [cols[sc["filed_under"]]] if sc.get("filed_under") in cols else []})
    return out


def plan_messages(request, history, cat, collections):
    lines = []
    for c in cat:
        extra = (" [just discussed]" if c.get("discussed") else "") + (" [just now]" if c["just_now"] else "") + (f" [filed in: {', '.join(c['filed_in'])}]" if c["filed_in"] else "")
        lines.append(f'{c["n"]}. "{c["title"]}" ({c["created"]}){extra}')
    convo = "\n".join(f"{h.get('who', 'user')}: {str(h.get('text', ''))[:300]}" for h in (history or [])[-HISTORY_LINES:])
    user = (f"Recent conversation:\n{convo or '(none)'}\n\nRequest: {request}\n\n"
            f"Notes:\n" + "\n".join(lines) + "\n\nExisting collections: "
            + (", ".join(f'"{c["name"]}"' for c in collections) or "(none)"))
    return [{"role": "system", "content": PLAN_SYSTEM_PROMPT}, {"role": "user", "content": user}]


def parse_plan(text):
    if not text:
        raise ValueError("empty reply")
    cleaned = re.sub(r"```(?:json)?", "", text)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON in the reply")
    data = json.loads(cleaned[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("not a plan")
    return data


def _said_recently(text, history):
    """A 'remember' must be the user's own words from the recent conversation
    (not something the model made up)."""
    words = [w for w in re.findall(r"[a-z0-9']+", (text or "").lower()) if w not in links.STOP]
    said = set(re.findall(r"[a-z0-9']+", " ".join(str(h.get("text", "")) for h in (history or [])[-HISTORY_LINES:]
                                                  if h.get("who") == "user").lower()))
    return bool(words) and sum(w in said for w in words) >= 0.8 * len(words)


def validate(data, request, cat, collections, history=None):
    """-> (steps, problems). Steps use record ids; anything unsafe is dropped
    and said out loud, never guessed at."""
    by_n = {c["n"]: c for c in cat}
    steps, problems = [], []
    for a in data.get("actions") or []:
        if not isinstance(a, dict) or a.get("do") not in ACTIONS:
            problems.append("an action I don't do")
            continue
        if a["do"] == "remember":
            text = str(a.get("text") or "").strip()[:500]
            if 0 in by_n:
                problems.append("more than one new note")
            elif not _said_recently(text, history):
                problems.append("a note to save that you didn't say")
            else:
                by_n[0] = {"n": 0, "id": None, "title": text, "new": True}
                steps.append({"do": "remember", "text": text, "notes": [by_n[0]]})
            continue
        nums = a.get("notes") if isinstance(a.get("notes"), list) else []
        bad = [n for n in nums if not isinstance(n, int) or n not in by_n]
        notes = []
        for n in nums:
            if isinstance(n, int) and n in by_n and by_n[n] not in notes:
                notes.append(by_n[n])
        if bad:
            problems.append(f"note number(s) {bad} that don't exist")
        if not notes or len(notes) > MAX_NOTES_PER_ACTION:
            continue
        step = {"do": a["do"], "notes": notes}
        if a["do"] in ("link", "unlink") and len(notes) < 2:
            problems.append(f"{a['do']} needs two notes")
            continue
        if a["do"] == "file":
            name = str(a.get("collection") or "").strip().strip("\"'")[:60]
            existing = next((c for c in collections if c["name"].lower() == name.lower()), None)
            if not existing and collections and name:
                close = difflib.get_close_matches(name.lower(), [c["name"].lower() for c in collections], 1, 0.85)
                existing = next((c for c in collections if close and c["name"].lower() == close[0]), None)
            if not name:
                problems.append("a filing with no name")
                continue
            if not existing and not _fuzzy_words_in(name, request):
                problems.append(f"a collection name ('{name}') you didn't say")
                continue
            step["collection"] = existing["name"] if existing else name
            step["request"] = request
            step["collection_id"] = existing["id"] if existing else None
        if a["do"] == "move":
            home = str(a.get("home") or "").upper()
            if home not in sorting.SORT_HOMES:
                problems.append(f"an unknown place ('{home}')")
                continue
            step["home"] = home
        steps.append(step)
    return steps, problems


# ---- carrying it out, with undo ---------------------------------------------------------
# One undo for everything that changes notes' records from a sentence (this
# plan route AND organise.py's own parser): take a copy of every record
# first, and afterwards keep only the ones that changed, plus anything new.

_UNDO = {}
_UNDO_LOCK = threading.Lock()


def begin(notes_dir=None):
    recs, _ = records.load_all(notes_dir)
    return copy.deepcopy(recs)


def commit(before, notes_dir=None, file_ops=None):
    """Registers an undo for everything that changed since begin(). Returns
    a token, or None if nothing changed. 3.8.0: file_ops = [{"path": note,
    "old": its bytes before, "remove": a copy to delete on undo (the bin)}]
    for changes to notes themselves (rename, edit, delete from the star menu)."""
    after, _ = records.load_all(notes_dir)
    changed = {i: before[i] for i in before if i in after and after[i] != before[i]}
    created = [{"id": i, "path": after[i]["path"]} for i in after if i not in before]
    if not changed and not created and not file_ops:
        return None
    token = uuid.uuid4().hex[:12]
    with _UNDO_LOCK:
        _UNDO[token] = {"snapshot": changed, "created": created, "files": file_ops or [], "at": time.time()}
        for old in sorted(_UNDO, key=lambda k: _UNDO[k]["at"])[:-UNDO_KEEP]:
            _UNDO.pop(old, None)
    return token


def _titles(notes):
    t = [f"'{n['title']}'" for n in notes]
    return t[0] if len(t) == 1 else ", ".join(t[:-1]) + " and " + t[-1]


def execute(steps, notes_dir=None, remember_fn=None):
    """Runs checked steps. Returns (spoken, undo_token)."""
    import organise
    root = notes_dir or records.NOTES_DIR
    before = begin(root)
    said = []
    for s in steps:                      # a new note first, so the rest can use it
        if s["do"] == "remember":
            if remember_fn is None:
                raise ValueError("can't save a new note from here")
            new = s["notes"][0]
            new["id"], new["path"] = remember_fn(s["text"])
            new["title"] = reviews._title(records.load_all(root)[0][new["id"]], root)
            said.append(f"Saved '{new['title']}'")
    for s in steps:
        if s["do"] == "remember":
            continue
        if s["do"] in ("link", "unlink"):
            links.apply(s["do"], s["notes"], root)
            said.append(("Linked " if s["do"] == "link" else "Unlinked ") + _titles(s["notes"]))
        elif s["do"] == "move":
            with records._LOCK:
                recs, _ = records.load_all(root)
                for n in s["notes"]:
                    sc = recs[n["id"]]
                    sc["home"], sc["status"] = s["home"], "sorted"
                    records._event(sc, "moved by you", home=s["home"])
                    records.save(sc, root)
            said.append(f"Moved {_titles(s['notes'])} to {sorting.HOME_WORDS.get(s['home'], s['home'])}")
        elif s["do"] == "file":
            title, new, _pid, kind = organise.file_under(s["collection"], s["notes"], root,
                                                         "project" if re.search(r"\bproject\b", s.get("request", ""), re.I) else "category")
            said.append(f"Filed {_titles(s['notes'])} under " + (f"a new {kind}, " if new else "") + f"'{title}'")
    return ". ".join(said) + ", sir.", commit(before, root)


def undo(token, notes_dir=None):
    root = notes_dir or records.NOTES_DIR
    with _UNDO_LOCK:
        u = _UNDO.pop(token, None)
    if not u:
        raise ValueError("that can't be undone any more (Jarvis was restarted, or it's already undone)")
    with records._LOCK:
        for op in u.get("files", []):              # notes' own text first
            if op.get("remove") and os.path.isfile(op["remove"]):
                os.remove(op["remove"])
            if op.get("old") is not None:
                os.makedirs(os.path.dirname(op["path"]), exist_ok=True)
                with open(op["path"], "wb") as f:
                    f.write(op["old"])
        for sc in u["snapshot"].values():
            records._event(sc, "undone by you")
            records.save(sc, root)
        for c in u["created"]:
            # A project, or a note saved from the chat, that Jarvis made for this
            # request moments ago -- removed because you said undo.
            rec_file = os.path.join(records.store_dir(root), "records", f"{c['id']}.json")
            note_file = records.abs_path(c["path"], root)
            for f in (rec_file, note_file):
                if os.path.isfile(f):
                    os.remove(f)
        gone = {c["id"] for c in u["created"]}
        if gone:
            recs, _ = records.load_all(root)
            for sc in recs.values():
                links_before, filed_before = len(sc.get("links", [])), sc.get("filed_under")
                sc["links"] = [l for l in sc.get("links", []) if l.get("target_id") not in gone]
                if sc.get("filed_under") in gone:
                    sc.pop("filed_under")
                if len(sc["links"]) != links_before or sc.get("filed_under") != filed_before:
                    records.save(sc, root)
    return "Undone, sir. Everything is back as it was."


def handle(request, history, call_brain, config, notes_dir=None, remember_fn=None, focus_id=None):
    """The whole round trip. Returns {"ok", "spoken", "undo"?, "question"?}."""
    cat = catalogue(notes_dir, focus_id=focus_id)
    if not cat:
        return {"ok": False, "spoken": "There are no notes to work with yet, sir."}
    cols = _collections(notes_dir)
    try:
        reply, model, _ = call_brain(config, plan_messages(request, history, cat, cols))
        data = parse_plan(reply)
    except (ValueError, RuntimeError) as e:
        return {"ok": False, "spoken": f"I couldn't work that out just now, sir ({e}). "
                "You can also say it directly, like: link mic and image reading."}
    if data.get("chat") is True and not data.get("actions"):
        return {"ok": False, "chat": True, "spoken": ""}
    steps, problems = validate(data, request, cat, cols, history)
    if not steps:
        q = data.get("question") if isinstance(data.get("question"), str) and data["question"].strip() else None
        why = f" (the plan had {', '.join(problems)})" if problems else ""
        return {"ok": False, "question": bool(q),
                "spoken": (q.strip() if q else f"I'm not sure which notes you mean, sir{why}. Name them and I'll do it.")}
    try:
        spoken, token = execute(steps, notes_dir, remember_fn)
    except (ValueError, OSError, RuntimeError) as e:
        return {"ok": False, "spoken": f"That didn't work, sir: {e}"}
    if problems:
        spoken += f" I skipped part of it: {', '.join(problems)}."
    return {"ok": True, "spoken": spoken, "undo": token, "model": model}
