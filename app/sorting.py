"""
sorting.py — Personal OS Phase 3 (2.5.0): sorting the INBOX, and linking.

When you say "sort my inbox", Jarvis sends the waiting notes to the AI
brain (several per request, to spare OpenRouter's free daily limit) and
asks what each one is: a task, an idea, a reflection, something to wonder
about...; where it belongs; who and what it mentions; whether you meant
to DO something or were just thinking out loud.

What comes back is treated as an INTERPRETATION, never as truth:
  - stored beside the note, with which model, when, and how confident it
    said it was; `confirmed` stays false until you confirm or correct it
  - your note is never edited
  - anything the model names (people, places, organisations, dates) must
    literally appear in your note, or it's dropped -- no invented facts
  - "I'd love to visit Japan" never becomes a task: task-like kinds are
    kept only when the model says you intend to act (spec §5)
  - unsure whether you meant to act -> it asks you, and leaves it
    uncommitted rather than guessing (spec §6, §27)
  - can't place it at all -> UNSORTED, shown to you, not filed
  - a second reading (model rotation means it may be a different model)
    that disagrees with the first -> it asks which is right

Then related notes are linked: notes that mention the same person, place
or organisation get a suggested link (by Jarvis, unconfirmed, with the
reason), which the galaxy draws. No text is ever appended to a note.

Standard library only; the AI call is passed in (server.call_brain), so
all of this is testable with a stand-in model.
"""

import datetime
import json
import re

import loops
import records

BATCH_SIZE = 8          # notes per request
MAX_BATCHES = 3         # per "sort my inbox" (so one run uses at most 3 requests)
UNSORTED_BELOW = 0.5    # confidence under this -> UNSORTED

SORT_HOMES = ("NOW", "PROJECTS", "AREAS", "KNOWLEDGE", "JOURNAL", "SOMEDAY", "ARCHIVE", "WONDER")
AREAS = ("Work", "Health & Fitness", "Home", "Relationships", "Finance", "Personal")
SEND_CHARS = 4000       # 5.0.0: most of any one note that goes to a model when sorting
KINDS = ("task", "project", "commitment", "waiting_for", "decision", "decision_to_confirm",
         "problem", "risk", "idea", "knowledge", "reflection", "experience", "goal",
         "future_possibility", "wonder", "purchase", "reminder", "person_context", "work_context")
# Kinds that would make Jarvis act on it later (chase it, remind you): only
# kept when you clearly meant to act.
ACTION_KINDS = ("task", "commitment", "reminder", "purchase", "waiting_for")
MEMORY_KINDS = ("fact", "preference", "current_state", "event", "decision", "goal",
                "reflection", "interest", "none")
INTENTIONS = ("act", "maybe", "none")
DATE_KINDS = ("deadline", "event", "mention")
# 2.9.0 (spec §20): obligations vs responsibilities vs choices vs desires vs curiosities.
NATURES = ("obligation", "responsibility", "choice", "desire", "curiosity")

HOME_WORDS = {
    "NOW": "Now", "PROJECTS": "Projects", "AREAS": "Areas", "KNOWLEDGE": "Knowledge",
    "JOURNAL": "Journal", "SOMEDAY": "Someday", "ARCHIVE": "Archive", "WONDER": "Wonder",
}

SORT_SYSTEM_PROMPT = """You help one person sort the notes they've captured into their personal organiser. \
You are given notes as JSON. For EACH note, return how you read it. You are interpreting, not deciding: \
be honest about uncertainty.

Homes (pick one):
- NOW: something they clearly intend to do soon.
- PROJECTS: an outcome that needs several steps.
- AREAS: an ongoing responsibility (work, health, home, relationships, finance, personal).
- KNOWLEDGE: reference information worth keeping (codes, facts, how-tos, preferences of people).
- JOURNAL: feelings, reflections, what happened, observations about their life.
- SOMEDAY: things they might do eventually, with no current intention to act.
- ARCHIVE: done or no longer relevant.
- WONDER: curiosity, fascination, places to visit, things to explore for enjoyment. Never a to-do list.

Rules:
- Do NOT turn every statement into a task. "I've always wanted to visit Japan" is WONDER or SOMEDAY, \
not a task. Only use intention "act" when the words show they mean to do it ("need to", "must", "tomorrow I'll").
- If you can't tell whether they meant to act or were just thinking, use intention "maybe".
- Keep a feeling as a feeling. Never rewrite someone's words into productivity language.
- A temporary state ("interested in astronomy this week", "tired today") is temporary: set temporary true.
- people, places, organisations and date "text" must be copied EXACTLY as written in the note. \
Never add anything that isn't in the note.
- question: only if the answer would change what should happen with the note; otherwise null. \
Short, plain, one question.
- confidence: 0 to 1, how sure you are of the home.
- nature: what kind of thing it is in their life: obligation (has to be done for others/rules), responsibility (ongoing duty they carry), choice (something they chose to take on), desire (something they want), curiosity (something that interests them), or null if unclear.
- Only if the note records a decision the person made: add "decision" with "what", "why", "alternatives", "constraints", "consequences" -- each an EXACT quote copied from the note (lists of quotes, empty if the note doesn't say). Never supply a reason they didn't write.

Reply with JSON only, no other text, in exactly this shape:
{"notes": [{"n": 1, "home": "WONDER", "area": null, "kinds": ["wonder"], "intention": "none",
 "memory": "interest", "temporary": false, "people": [], "places": ["Japan"], "organisations": [],
 "dates": [{"text": "tomorrow", "kind": "deadline", "iso": "2026-09-24"}], "themes": ["travel"],
 "urgency": "unknown", "confidence": 0.9, "question": null, "nature": "desire"}]}

kinds may be: """ + ", ".join(KINDS) + """.
memory may be: """ + ", ".join(MEMORY_KINDS) + """.
area may be: """ + ", ".join(AREAS) + """, or null.
date kind may be: deadline, event, mention; iso is your best YYYY-MM-DD reading of it using the note's \
captured date, or null.
urgency may be: high, normal, low, unknown."""


# ---- reading a note ----------------------------------------------------------------

BOILERPLATE = re.compile(r"^(#\s.*|Captured \d{4}-\d{2}-\d{2}.*|Added \d{4}-\d{2}-\d{2}.*)$")


def note_words(path, notes_dir=None):
    """The note's own words: without Jarvis's title and date lines."""
    text = records._read_text(records.abs_path(path, notes_dir))
    lines = [ln for ln in text.splitlines() if not BOILERPLATE.match(ln.strip())]
    return "\n".join(lines).strip()


def _captured_date(sc):
    return (sc.get("created") or "")[:10] or None


# ---- parsing and checking the model's reply ---------------------------------------

def parse_reply(text):
    """The model's JSON, even wrapped in ``` fences or a sentence. Raises
    ValueError if there's no usable JSON at all."""
    if not text:
        raise ValueError("empty reply")
    cleaned = re.sub(r"```(?:json)?", "", text)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON in the reply")
    data = json.loads(cleaned[start:end + 1])
    notes = data.get("notes") if isinstance(data, dict) else None
    if not isinstance(notes, list):
        raise ValueError("reply has no 'notes' list")
    return notes


def _str_list(v, limit=10):
    if not isinstance(v, list):
        return []
    return [s.strip() for s in v if isinstance(s, str) and s.strip()][:limit]


def _in_text(item, text):
    return item.lower() in text.lower()


def validate(raw, words):
    """Turns one raw model answer into a checked interpretation. Anything
    the model named that isn't in the note is dropped (and counted), so a
    made-up person or date can never enter the record."""
    dropped = []
    home = raw.get("home") if raw.get("home") in SORT_HOMES else None
    area = raw.get("area") if raw.get("area") in AREAS else None
    kinds = [k for k in _str_list(raw.get("kinds"), 8) if k in KINDS]
    intention = raw.get("intention") if raw.get("intention") in INTENTIONS else "maybe"
    memory = raw.get("memory") if raw.get("memory") in MEMORY_KINDS else "none"
    temporary = raw.get("temporary") is True
    try:
        confidence = float(raw.get("confidence"))
        confidence = min(1.0, max(0.0, confidence))
    except (TypeError, ValueError):
        confidence = 0.0

    entities = {}
    for field in ("people", "places", "organisations"):
        kept = []
        for item in _str_list(raw.get(field)):
            (kept if _in_text(item, words) else dropped).append(item)
        entities[field] = kept
    dates = []
    for d in raw.get("dates") if isinstance(raw.get("dates"), list) else []:
        if not isinstance(d, dict) or not isinstance(d.get("text"), str):
            continue
        if not _in_text(d["text"], words):
            dropped.append(d["text"])
            continue
        iso = d.get("iso")
        if not (isinstance(iso, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso)):
            iso = None
        dates.append({"text": d["text"], "kind": d.get("kind") if d.get("kind") in DATE_KINDS else "mention",
                      "iso": iso})
    themes = [t for t in _str_list(raw.get("themes"), 5) if len(t.split()) <= 3]
    urgency = raw.get("urgency") if raw.get("urgency") in ("high", "normal", "low", "unknown") else "unknown"
    question = raw.get("question") if isinstance(raw.get("question"), str) and raw["question"].strip() else None
    if question and len(question) > 240:
        question = question[:240].rstrip() + "…"

    # The Japan rule: nothing becomes something to act on unless you meant it.
    held_back = []
    if intention != "act":
        held_back = [k for k in kinds if k in ACTION_KINDS]
        kinds = [k for k in kinds if k not in ACTION_KINDS]
        if home == "NOW":
            home = "SOMEDAY"
    if home == "WONDER":          # "Do not turn WONDER into another task list"
        kinds = [k for k in kinds if k not in ACTION_KINDS]

    decision = loops.check_decision(raw.get("decision"), words) if "decision" in kinds else None
    nature = raw.get("nature") if raw.get("nature") in NATURES else None
    return {
        "decision": decision, "nature": nature,
        "home": home, "area": area, "kinds": kinds, "intention": intention, "memory": memory,
        "temporary": temporary, **entities, "dates": dates, "themes": themes, "urgency": urgency,
        "confidence": round(confidence, 2), "question": question,
        "held_back_action_kinds": held_back, "dropped_not_in_note": dropped,
    }


def decide(v, previous=None):
    """(status, question) for a checked interpretation.
      sorted              -> filed in its home, awaiting your confirmation
      needs_clarification -> it asks you, and files nothing yet
      unsorted            -> it couldn't place it"""
    if v["home"] is None or v["confidence"] < UNSORTED_BELOW:
        return "unsorted", None
    if previous and previous.get("home") and previous["home"] != v["home"]:
        a, b = HOME_WORDS[previous["home"]], HOME_WORDS[v["home"]]
        return "needs_clarification", {
            "text": f"Two readings disagree on this one: {a} or {b}?",
            "options": [{"label": a, "action": "move", "home": previous["home"]},
                        {"label": b, "action": "move", "home": v["home"]}],
        }
    if v["intention"] == "maybe" and v["held_back_action_kinds"]:
        return "needs_clarification", {
            "text": v["question"] or ("I'm not certain whether this is something you mean to do or just "
                                      "a thought, so I've left it uncommitted. Treat it as something to do?"),
            "options": [{"label": "Yes, something to do", "action": "act"},
                        {"label": "Just a thought", "action": "thought"}],
        }
    if v["question"]:
        return "needs_clarification", {
            "text": v["question"],
            "options": [{"label": f"File in {HOME_WORDS[v['home']]}", "action": "move", "home": v["home"]}]
                       + [{"label": HOME_WORDS[h], "action": "move", "home": h}
                          for h in ("SOMEDAY", "JOURNAL", "KNOWLEDGE") if h != v["home"]][:2],
        }
    return "sorted", None


# ---- the run ------------------------------------------------------------------------

def _latest_sorting(sc):
    for it in reversed(sc.get("interpretations", [])):
        if it.get("kind") == "sorting":
            return it
    return None


def waiting(notes_dir=None):
    recs, _ = records.load_all(notes_dir)
    out = [sc for sc in recs.values()
           if not sc.get("missing") and sc.get("status") == "unprocessed" and sc.get("home") == "INBOX"]
    out.sort(key=lambda s: s.get("created") or "")
    return out


def sort_inbox(call_brain, config, notes_dir=None, today=None, max_batches=MAX_BATCHES,
               only_ids=None, link=True):
    """Sorts waiting inbox notes. Returns a summary dict. A model failure
    stops the run and changes nothing for the notes in that batch."""
    today = today or datetime.date.today()
    todo = waiting(notes_dir)
    if only_ids is not None:
        recs, _ = records.load_all(notes_dir)
        todo = [recs[i] for i in only_ids if i in recs and not recs[i].get("missing")]
    summary = {"sorted": [], "needs_you": [], "unsorted": [], "not_read": [], "error": None,
               "models": [], "remaining": 0, "links_added": 0}
    batches = [todo[i:i + BATCH_SIZE] for i in range(0, len(todo), BATCH_SIZE)]
    for bi, batch in enumerate(batches):
        if bi >= max_batches:
            summary["remaining"] += len(batch)
            continue
        payload, words_by_n = [], {}
        for n, sc in enumerate(batch, 1):
            try:
                words = note_words(sc["path"], notes_dir)
            except OSError:
                summary["not_read"].append(sc["path"])
                continue
            words_by_n[n] = (sc, words)
            # 5.0.0: only what's needed goes out -- a long imported file is sent as its
            # opening part, and passwords/keys/card numbers are hidden (brief 3 §24).
            import brain
            sent, _hidden = brain.redact(words if len(words) <= SEND_CHARS else
                                         words[:SEND_CHARS] + " […the rest of this note isn't shown]")
            payload.append({"n": n, "captured": _captured_date(sc), "source": sc.get("source"),
                            "text": sent})
        if not payload:
            continue
        messages = [
            {"role": "system", "content": SORT_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({"today": today.isoformat(), "notes": payload},
                                                   ensure_ascii=False)},
        ]
        try:
            reply, model_used, _note = call_brain(config, messages)
            answers = parse_reply(reply)
        except RuntimeError as e:
            summary["error"] = str(e)
            summary["remaining"] += sum(len(b) for b in batches[bi:])
            break
        except (ValueError, json.JSONDecodeError) as e:
            summary["error"] = f"the answer came back in a form I couldn't read ({e})"
            summary["remaining"] += sum(len(b) for b in batches[bi:])
            break
        summary["models"].append(model_used)
        answered = {}
        for raw in answers:
            if isinstance(raw, dict) and isinstance(raw.get("n"), int) and raw["n"] in words_by_n:
                answered.setdefault(raw["n"], raw)
        for n, (sc, words) in words_by_n.items():
            if n not in answered:
                summary["not_read"].append(sc["path"])
                continue
            status = apply_reading(sc, validate(answered[n], words), model_used, notes_dir)
            key = {"sorted": "sorted", "needs_clarification": "needs_you", "unsorted": "unsorted"}[status]
            summary[key].append(sc["id"])
    # (preflight sorts a throwaway test note with link=False, so it never
    # adds a link to one of your real notes)
    summary["links_added"] = link_related(notes_dir) if link else 0
    return summary


def apply_reading(sc, v, model_used, notes_dir=None):
    """Stores one checked reading beside the note and sets its status."""
    with records._LOCK:
        fresh, _ = records.load_all(notes_dir)
        sc = fresh.get(sc["id"], sc)
        previous = _latest_sorting(sc)
        # (The earlier reading's home lives in its "value"; found by the
        # two-models test, which failed until this looked in the right place.)
        prev_unconfirmed = previous["value"] if previous and not previous.get("confirmed") else None
        status, question = decide(v, prev_unconfirmed)
        sc.setdefault("interpretations", []).append({
            "kind": "sorting", "value": v, "model": model_used, "at": records.now_iso(),
            "confidence": v["confidence"], "confirmed": False,
        })
        sc["status"] = status
        sc["question"] = question
        if status == "sorted":
            sc["home"] = v["home"]
        else:
            sc["home"] = "INBOX"
        loops.refresh_loop(sc)   # 2.6.0: an open loop if it's something unresolved
        records._event(sc, "sorted by Jarvis" if status == "sorted" else
                       ("Jarvis asked you" if status == "needs_clarification" else "Jarvis couldn't place it"),
                       model=model_used, home=v["home"], confidence=v["confidence"])
        records.save(sc, notes_dir)
        return status


# ---- you decide ---------------------------------------------------------------------

def decide_for_you(record_id, action, home=None, notes_dir=None):
    """Your answer or correction. Returns the updated record.
      confirm  -- the reading is right (home kept)
      move     -- file it in `home` instead (or answer a which-one question)
      act      -- yes, it's something to do (its held-back task kinds restored, home NOW)
      thought  -- just a thought (nothing to act on; home kept, or SOMEDAY if it was NOW)
      inbox    -- put it back in the inbox, unsorted"""
    with records._LOCK:
        recs, _ = records.load_all(notes_dir)
        sc = recs.get(record_id)
        if sc is None or sc.get("missing"):
            raise ValueError("that note isn't there any more")
        reading = _latest_sorting(sc)
        if action == "inbox":
            sc["home"], sc["status"], sc["question"] = "INBOX", "unprocessed", None
            records._event(sc, "you put it back in the inbox")
            records.save(sc, notes_dir)
            return sc
        if reading is None:
            raise ValueError("Jarvis hasn't sorted that note yet")
        v = reading["value"]
        if action == "confirm":
            new_home = sc["home"] if sc["home"] in SORT_HOMES else v.get("home")
            if new_home not in SORT_HOMES:
                raise ValueError("there's no home to confirm; choose one")
        elif action == "move":
            if home not in SORT_HOMES:
                raise ValueError("unknown home")
            new_home = home
        elif action == "act":
            new_home = "NOW"
            v["kinds"] = sorted(set(v.get("kinds", [])) | set(v.get("held_back_action_kinds") or ["task"]))
            v["intention"] = "act"
        elif action == "thought":
            new_home = v.get("home") if v.get("home") not in (None, "NOW") else "SOMEDAY"
            v["intention"] = "none"
            v["kinds"] = [k for k in v.get("kinds", []) if k not in ACTION_KINDS]
        else:
            raise ValueError("unknown action")
        corrected = new_home != v.get("home") or action in ("act", "thought")
        reading["confirmed"] = True
        reading["corrected_by_you"] = corrected
        reading["confirmed_at"] = records.now_iso()
        sc["home"], sc["status"], sc["question"] = new_home, "sorted", None
        loops.refresh_loop(sc)
        records._event(sc, "you confirmed it" if not corrected else "you corrected it",
                       home=new_home, action=action)
        records.save(sc, notes_dir)
        return sc


# ---- linking ------------------------------------------------------------------------

def _entities(sc):
    r = _latest_sorting(sc)
    if not r:
        return {}
    v = r["value"]
    out = {}
    for field in ("people", "places", "organisations"):
        for e in v.get(field, []):
            out[e.lower()] = e
    return out


def link_related(notes_dir=None):
    """Suggests links between notes that mention the same person, place or
    organisation. Stored in both notes' records (by Jarvis, unconfirmed,
    with the reason). Never touches the notes. Returns links added."""
    added = 0
    with records._LOCK:
        recs, _ = records.load_all(notes_dir)
        live = [sc for sc in recs.values() if not sc.get("missing")]
        ents = {sc["id"]: _entities(sc) for sc in live}
        changed = set()
        for i, a in enumerate(live):
            for b in live[i + 1:]:
                shared = sorted(set(ents[a["id"]]) & set(ents[b["id"]]))
                if not shared:
                    continue
                if b["id"] in a.get("unlinked", []) or a["id"] in b.get("unlinked", []):
                    continue          # 3.6.0: you unlinked these; don't suggest it again
                names = [ents[a["id"]][k] for k in shared]
                reason = "both mention " + ", ".join(names)
                new_pair = False
                for x, y in ((a, b), (b, a)):
                    if any(l.get("target_id") == y["id"] and l.get("kind") == "related"
                           for l in x.get("links", [])):
                        continue
                    x.setdefault("links", []).append({
                        "kind": "related", "target_id": y["id"], "target_path": y["path"],
                        "by": "jarvis", "confirmed": False, "reason": reason, "at": records.now_iso(),
                    })
                    changed.add(x["id"])
                    new_pair = True
                added += 1 if new_pair else 0
        for sc in live:
            if sc["id"] in changed:
                records.save(sc, notes_dir)
    return added


# ---- what Jarvis says ------------------------------------------------------------

def spoken_summary(summary, titles):
    """Calm, short, and never a telling-off."""
    if summary["error"] and not (summary["sorted"] or summary["needs_you"] or summary["unsorted"]):
        return f"I couldn't sort anything just now, sir: {summary['error']}. Your notes are untouched."
    parts = []
    n_sorted, n_ask, n_uns = len(summary["sorted"]), len(summary["needs_you"]), len(summary["unsorted"])
    if not (n_sorted or n_ask or n_uns):
        return "Nothing waiting to be sorted, sir."
    if n_sorted:
        parts.append(f"{n_sorted} filed")
    if n_ask:
        parts.append(f"{n_ask} I'd like your word on")
    if n_uns:
        parts.append(f"{n_uns} I couldn't place")
    line = "Sorted, sir: " + ", ".join(parts) + "."
    if n_ask:
        first = titles.get(summary["needs_you"][0])
        if first:
            line += f" The first question is about '{first}'."
    if summary["remaining"]:
        line += f" {summary['remaining']} more are waiting; say \"sort my inbox\" again for the rest."
    if summary["error"]:
        line += f" I stopped early: {summary['error']}."
    return line
