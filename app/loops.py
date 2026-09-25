"""
loops.py — Personal OS Phase 4 (2.6.0): open loops, projects, decisions.

No separate task app (integration rules §8): everything here is a field on
the records that already sit beside your notes. Your notes are never edited
-- except that creating a PROJECT writes one new note for it.

OPEN LOOPS (spec §14) -- anything unresolved that's taking up head space.
A sorted note becomes a loop when its reading says so:
  to do      task / commitment / reminder (only if you meant to act)
  to buy     purchase (same)
  waiting    waiting on someone else (same)
  to decide  a decision still to make
  to solve   a problem or risk
Each loop: kind, state (open / done / dropped), due date (only a deadline
date that was actually written in your note), project, history.

PROJECTS (spec §13) -- an outcome needing several steps. A project is a
note of its own (home PROJECTS) with: outcome, status (active / paused /
done), target date, and its member loops. "Next action" is its earliest-due
open loop -- worked out, not stored, so it can't go stale. When three or
more open loops share a person, place or organisation and aren't in a
project yet, Jarvis asks: "These look like one project -- combine them?"

DECISIONS (spec §15) -- what was decided, when, why, alternatives,
constraints, expected consequences: ONLY as exact quotes from your own note.
"Why did I decide X?" is answered from those quotes, word for word, with no
AI in between -- so a reason you never wrote down can't be invented.

Standard library only.
"""

import datetime
import os
import re

import records

LOOP_KINDS = {
    "task": "to do", "commitment": "to do", "reminder": "to do",
    "purchase": "to buy", "waiting_for": "waiting",
    "decision_to_confirm": "to decide", "problem": "to solve", "risk": "to solve",
}
LOOP_ORDER = ("to do", "waiting", "to decide", "to solve", "to buy")
PROJECT_STATUSES = ("active", "paused", "done", "abandoned")     # 4.5.0: + abandoned
SUGGEST_MIN = 3


def _reading(sc):
    for it in reversed(sc.get("interpretations", [])):
        if it.get("kind") == "sorting":
            return it
    return None


def loop_kind_for(v):
    """The loop a reading implies, or None. (Action kinds were already
    held back by sorting when you didn't mean to act.)"""
    for k in v.get("kinds", []):
        if k in LOOP_KINDS:
            return LOOP_KINDS[k]
    return None


def _due_from(v):
    for d in v.get("dates", []):
        if d.get("kind") == "deadline" and d.get("iso"):
            return d["iso"]
    return None


def refresh_loop(sc):
    """Creates or updates a record's loop from its latest reading. Never
    reopens a loop you closed, never overrides a due date you set."""
    r = _reading(sc)
    if not r or sc.get("status") != "sorted":
        return False
    kind = loop_kind_for(r["value"])
    loop = sc.get("loop")
    if kind is None:
        if loop and loop.get("state") == "open" and loop.get("by") == "jarvis":
            loop["state"] = "dropped"
            loop["closed_at"] = records.now_iso()
            records._event(sc, "loop dropped (no longer reads as something to act on)")
            return True
        return False
    if loop is None:
        sc["loop"] = {"kind": kind, "state": "open", "due": _due_from(r["value"]), "due_set_by": "note",
                      "project": None, "by": "jarvis", "opened_at": records.now_iso()}
        records._event(sc, "loop opened", kind=kind)
        return True
    if loop.get("state") == "open" and loop.get("kind") != kind:
        loop["kind"] = kind
        return True
    return False


def refresh_all(notes_dir=None):
    with records._LOCK:
        recs, _ = records.load_all(notes_dir)
        n = 0
        for sc in recs.values():
            if not sc.get("missing") and refresh_loop(sc):
                records.save(sc, notes_dir)
                n += 1
        return n


def _title(sc, notes_dir):
    try:
        return records.title_of(sc["path"], records._read_text(records.abs_path(sc["path"], notes_dir)))
    except OSError:
        return sc["path"]


def overview(notes_dir=None, today=None):
    """Open loops grouped, projects with their next action, and project
    suggestions."""
    today = (today or datetime.date.today()).isoformat()
    recs, _ = records.load_all(notes_dir)
    live = {i: sc for i, sc in recs.items() if not sc.get("missing")}
    loops = []
    for sc in live.values():
        loop = sc.get("loop")
        if not loop or loop.get("state") != "open":
            continue
        r = _reading(sc)
        loops.append({
            "id": sc["id"], "title": _title(sc, notes_dir), "kind": loop["kind"], "due": loop.get("due"),
            "overdue": bool(loop.get("due") and loop["due"] < today),
            "project": loop.get("project"),
            # names shared across loops drive "these look like one project"
            "people": sum(((r or {}).get("value", {}).get(f, []) for f in ("people", "places", "organisations")), []),
            "created": sc.get("created"), "confirmed": bool(r and r.get("confirmed")),
        })
    loops.sort(key=lambda l: (l["due"] is None, l["due"] or "", l["created"] or ""))
    projects, categories = [], []
    for sc in live.values():
        p = sc.get("project_meta")
        if not p:
            continue
        if p.get("kind") == "category":        # 3.8.0: categories are listed separately
            categories.append({"id": sc["id"], "title": _title(sc, notes_dir),
                               "count": sum(1 for s2 in live.values() if s2.get("filed_under") == sc["id"])})
            continue
        members = [l for l in loops if l["project"] == sc["id"]]
        activity = [s.get("history", [{}])[-1].get("at") for s in live.values()
                    if (s.get("loop") or {}).get("project") == sc["id"]] + [sc.get("history", [{}])[-1].get("at")]
        projects.append({
            "id": sc["id"], "title": _title(sc, notes_dir), "outcome": p.get("outcome"),
            "status": p.get("status"), "target": p.get("target"), "created": sc.get("created"),
            "open_loops": len(members), "next_action": members[0]["title"] if members else None,
            "filed": sum(1 for s2 in live.values() if s2.get("filed_under") == sc["id"]),   # 3.7.0
            "last_activity": max([a for a in activity if a] or [None], key=lambda a: a or ""),
        })
    categories.sort(key=lambda c: c["title"].lower())
    return {"loops": loops, "projects": projects, "categories": categories, "suggestions": suggest_projects(loops),
            "counts": {k: sum(1 for l in loops if l["kind"] == k) for k in LOOP_ORDER}}


def suggest_projects(loops):
    """Open loops outside any project that share a person (or place /
    organisation): three or more -> one suggestion."""
    by_name = {}
    for l in loops:
        if l["project"]:
            continue
        for p in l["people"]:
            by_name.setdefault(p, []).append(l)
    out, used = [], set()
    for name, group in sorted(by_name.items(), key=lambda kv: -len(kv[1])):
        ids = [l["id"] for l in group if l["id"] not in used]
        if len(ids) >= SUGGEST_MIN:
            used.update(ids)
            out.append({"because": f"they all mention {name}", "name": name, "record_ids": ids,
                        "titles": [l["title"] for l in group if l["id"] in ids],
                        "question": f"These {len(ids)} look like one project. They all mention {name}. Combine them?"})
    return out


def update_loop(record_id, action, due=None, notes_dir=None):
    """done / drop / reopen / due (YYYY-MM-DD, or None to clear)."""
    with records._LOCK:
        recs, _ = records.load_all(notes_dir)
        sc = recs.get(record_id)
        if not sc or sc.get("missing") or not sc.get("loop"):
            raise ValueError("that isn't an open loop")
        loop = sc["loop"]
        if action in ("done", "drop"):
            loop["state"] = "done" if action == "done" else "dropped"
            loop["closed_at"] = records.now_iso()
        elif action == "reopen":
            loop["state"], loop["closed_at"] = "open", None
        elif action == "due":
            if due is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(due)):
                raise ValueError("a date looks like 2026-10-01")
            loop["due"], loop["due_set_by"] = due, "you"
        else:
            raise ValueError("unknown action")
        loop["by"] = "you"      # once you've touched it, Jarvis won't drop it by itself
        records._event(sc, f"loop {action}", due=due) if action == "due" else records._event(sc, f"loop {action}")
        records.save(sc, notes_dir)
        return sc


def create_project(name, record_ids, outcome=None, target=None, notes_dir=None, kind="project"):
    """Writes a new project note (notes/projects/<slug>.md) and puts the
    given loops in it. Returns the project's record.
    3.8.0: kind="category" makes a CATEGORY instead (notes/categories/):
    somewhere to file notes, with no outcome, status or deadline, and never
    listed as a project or flagged as stalled."""
    name = (name or "").strip()
    if not name:
        raise ValueError("a project needs a name")
    if target is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(target)):
        raise ValueError("a target date looks like 2026-10-01")
    root = notes_dir or records.NOTES_DIR
    with records._LOCK:
        recs, _ = records.load_all(root)
        members = [recs[i] for i in record_ids or [] if i in recs and recs[i].get("loop")]
        folder = os.path.join(root, "categories" if kind == "category" else "projects")
        os.makedirs(folder, exist_ok=True)
        slug = "-".join(re.findall(r"[a-z0-9]+", name.lower())[:6]) or "project"
        path = os.path.join(folder, f"{slug}.md")
        n = 1
        while os.path.exists(path):
            n += 1
            path = os.path.join(folder, f"{slug}-{n}.md")
        today = datetime.date.today().isoformat()
        lines = [f"# {name}", "", f"Added {today}: a {'category' if kind == 'category' else 'project'} you made in Jarvis."]
        if outcome:
            lines += ["", f"Outcome: {outcome.strip()}"]
        if members:
            lines += ["", "Started from:"] + [f"- {_title(m, root)}" for m in members]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        sc = records.create_for_file(path, source="file", event=f"{kind} created by you",
                                     notes_dir=root, name=name)
        sc["home"], sc["status"] = ("AREAS" if kind == "category" else "PROJECTS"), "sorted"
        sc["project_meta"] = {"outcome": (outcome or "").strip() or None, "status": "active", "target": target}
        if kind == "category":
            sc["project_meta"]["kind"] = "category"
        records.save(sc, root)
        for m in members:
            m["loop"]["project"] = sc["id"]
            m["loop"]["by"] = "you"
            records._event(m, "added to a project", project=name)
            records.save(m, root)
        return sc


def update_project(project_id, notes_dir=None, **changes):
    with records._LOCK:
        recs, _ = records.load_all(notes_dir)
        sc = recs.get(project_id)
        if not sc or not sc.get("project_meta"):
            raise ValueError("that isn't a project")
        meta = sc["project_meta"]
        if "status" in changes:
            if changes["status"] not in PROJECT_STATUSES:
                raise ValueError("status is active, paused, done or abandoned")
            meta["status"] = changes["status"]
        if "outcome" in changes:
            meta["outcome"] = (changes["outcome"] or "").strip() or None
        if "target" in changes:
            t = changes["target"]
            if t is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(t)):
                raise ValueError("a target date looks like 2026-10-01")
            meta["target"] = t
        if "add" in changes:
            m = recs.get(changes["add"])
            if not m or not m.get("loop"):
                raise ValueError("that isn't a loop")
            m["loop"]["project"] = project_id
            records._event(m, "added to a project", project=project_id)
            records.save(m, notes_dir)
        records._event(sc, "project updated", **{k: v for k, v in changes.items() if k != "add"})
        records.save(sc, notes_dir)
        return sc


# ---- decisions --------------------------------------------------------------

DECISION_FIELDS = ("why", "alternatives", "constraints", "consequences")


def check_decision(raw, words):
    """Keeps only exact quotes from your note. 'what' falls back to your
    whole note; a reason that isn't in the note is dropped, never kept."""
    if not isinstance(raw, dict):
        return None
    low = words.lower()
    def quotes(v):
        return [q.strip() for q in (v if isinstance(v, list) else []) if isinstance(q, str)
                and q.strip() and q.strip().lower() in low][:5]
    what = raw.get("what") if isinstance(raw.get("what"), str) and raw["what"].strip().lower() in low else None
    out = {"what": (what or words).strip()}
    for f in DECISION_FIELDS:
        out[f] = quotes(raw.get(f))
    return out


def _fmt_day(iso):
    try:
        d = datetime.date.fromisoformat((iso or "")[:10])
        return f"{d.day} {d.strftime('%B %Y')}"
    except ValueError:
        return "an unknown date"


def why(query, notes_dir=None):
    """"Why did I decide X?" -- best-matching recorded decision, answered
    only with your own words. Returns (spoken, record_id or None)."""
    words = set(re.findall(r"[a-z0-9]+", (query or "").lower())) - {
        "why", "did", "i", "decide", "choose", "chose", "pick", "picked", "go", "with", "to", "the",
        "a", "an", "on", "about", "that", "my", "me", "we", "was", "it", "for", "decision"}
    recs, _ = records.load_all(notes_dir)
    best, best_score = None, 0
    for sc in recs.values():
        r = _reading(sc)
        d = (r or {}).get("value", {}).get("decision")
        if sc.get("missing") or not d:
            continue
        text = " ".join([d["what"]] + sum((d[f] for f in DECISION_FIELDS), [])).lower()
        score = len(words & set(re.findall(r"[a-z0-9]+", text)))
        if score > best_score:
            best, best_score = (sc, d), score
    if not best:
        return ("I don't have a recorded decision about that, sir. If you note one down with your "
                "reasons, I'll keep them for next time."), None
    sc, d = best
    line = f"On {_fmt_day(sc.get('created'))} you decided: \"{d['what']}\"."
    if d["why"]:
        line += " Your reason at the time: " + "; ".join(f"\"{q}\"" for q in d["why"]) + "."
    else:
        line += " You didn't write down why, and I won't guess."
    if d["alternatives"]:
        line += " You'd considered: " + "; ".join(f"\"{q}\"" for q in d["alternatives"]) + "."
    if d["constraints"]:
        line += " Constraints you noted: " + "; ".join(f"\"{q}\"" for q in d["constraints"]) + "."
    return line, sc["id"]


def spoken_loops(ov):
    loops = ov["loops"]
    if not loops:
        return "No open loops, sir. Nothing's waiting on you."
    c = ov["counts"]
    parts = [f"{c[k]} {k}" for k in LOOP_ORDER if c.get(k)]
    line = f"{len(loops)} open loop{'s' if len(loops) != 1 else ''}, sir: " + ", ".join(parts) + "."
    overdue = [l for l in loops if l["overdue"]]
    if overdue:
        line += f" {len(overdue)} past {'its' if len(overdue) == 1 else 'their'} date, the first being '{overdue[0]['title']}'."
    elif loops[0]["due"]:
        line += f" Next due: '{loops[0]['title']}' on {_fmt_day(loops[0]['due'])}."
    if ov["suggestions"]:
        line += " " + ov["suggestions"][0]["question"]
    return line


def spoken_waiting(ov):
    w = [l for l in ov["loops"] if l["kind"] == "waiting"]
    if not w:
        return "You're not waiting on anyone that I know of, sir."
    return f"You're waiting on {len(w)} thing{'s' if len(w) != 1 else ''}: " + ", ".join(f"'{l['title']}'" for l in w[:4]) + "."
