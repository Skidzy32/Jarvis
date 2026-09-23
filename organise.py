"""
organise.py — 3.7.0: take a note, link it, file it, in one sentence.

  "I need a new mic"
  "link that in with the two existing notes and then file all 3 as
   Jarvis Maintenance/improvements"

is: save "I need a new mic" (if it wasn't saved), link it with the other
two notes, and file all three under a project called "Jarvis
Maintenance/improvements" (made if it doesn't exist). Jarvis's own code
does all of it. The AI is only asked to *interpret* a sentence this code
can't parse, and everything it says is checked: note numbers must exist,
and a project name must be words you actually said (or an existing
project). It can never invent a note or a name.

What you can refer to:
  that / this / it / the last note     -> your last note; if your last
                                          message went to chat unsaved, it
                                          is saved first
  the two existing / other notes, both,
  the others, the rest                 -> the other notes (most recent first)
  all 3 / all of them / them / these   -> everything named so far
  part of a title                      -> that note (see links.py)

Filing changes where a note is FILED (its record), never the note itself:
the note moves to Projects, is linked to the project's star (so the galaxy
shows them together), and any open loop in it joins the project.
Standard library only.
"""

import json
import re

import links
import loops
import records
import reviews

NUM = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "both": 2}
LINK_VERB = r"(?:link(?:ed|ing)?|connect(?:ed|ing)?|join(?:ed)?|tie[d]?|merge[d]?\s+the\s+links?)"
FILE_VERB = r"(?:file[d]?|filing|group(?:ed)?|categori[sz]e[d]?|organi[sz]e[d]?|put|move[d]?|tag(?:ged)?|label(?:l?ed)?|add(?:ed)?|place[d]?|sort(?:ed)?)"
PREP = r"(?:as|under|in|into|to|within)"
NAME_LEAD = r"(?:(?:a|the|my|new|a\s+new)\s+)*(?:(?:project|folder|category|group|section|collection|file)\s+)?(?:(?:called|named|titled)\s+)?"
FILE_RE = re.compile(rf"\b(?P<verb>{FILE_VERB})\b(?P<what>(?:(?!\b{PREP}\b).)*?)\b{PREP}\s+{NAME_LEAD}(?P<name>[^?!]+?)\s*[.?!]*$", re.I)
PRON_RE = re.compile(r"^(?:that|this|it|that one|this one|that note|this note|the (?:last|latest|new|newest|new) "
                     r"(?:one|note|capture|star)|what i (?:just )?said|my last (?:note|message)|the new note)$", re.I)
OTHERS_RE = re.compile(r"^(?:the\s+|my\s+)?(?:(?P<n>two|three|four|five|six|\d)\s+)?(?:other|existing|previous|older|earlier|"
                       r"remaining|old)(?:\s+(?:notes?|ones?|stars?))?$|^(?:the\s+)?(?:others|rest)$|^(?:the\s+)?(?P<n2>two|three|\d)\s+(?:notes?|ones?|stars?)$|"
                       r"^both(?:\s+(?:of\s+)?(?:the\s+)?(?:other\s+|existing\s+)?(?:notes?|ones?|stars?))?$", re.I)
ALL_RE = re.compile(r"^(?:all\s+(?:\d+|two|three|four|five|six|of\s+them|of\s+these|of\s+those|together)|all|them|these|those|"
                    r"them\s+all|these\s+\w+|those\s+\w+|everything|the\s+lot|the\s+\d+\s+of\s+them|both)$", re.I)
FILLER_RE = re.compile(r"^(?:(?:and\s+)?then\s+|and\s+|also\s+|please\s+|jarvis[,\s]+|can\s+you\s+|could\s+you\s+|would\s+you\s+|"
                       r"i\s+want\s+|i'?d\s+like\s+|i\s+need\s+|to\s+have\s+|to\s+get\s+|to\s+)+", re.I)
TRAIL_RE = re.compile(r"[\s,;]*(?:(?:and\s+)?then|and|also|please|,)[\s,;]*$", re.I)


def is_organise(text):
    t = text.lower()
    if re.search(r"\b(together|project|folder)\b", t) and re.search(r"\b(notes?|stars?|things?|that|it|them|these|those)\b", t):
        return True
    return bool(re.search(rf"\b{LINK_VERB}\b", t) or (re.search(rf"\b{FILE_VERB}\b", t) and re.search(rf"\b{PREP}\b", t)
                and re.search(r"\b(that|this|it|them|these|those|all|notes?|stars?|both|project|folder)\b", t)))


def _num(s):
    if not s:
        return None
    return int(s) if s.isdigit() else NUM.get(s.lower())


class Ctx:
    """What 'that', 'the others' and 'all 3' point at while one sentence is handled."""
    def __init__(self, notes_dir, last_id=None):
        self.notes_dir = notes_dir
        self.cat = [c for c in links._catalogue(notes_dir)]
        recs, _ = records.load_all(notes_dir)
        self.recs = recs
        self.cat = [c for c in self.cat if not recs[c["id"]].get("project_meta") and recs[c["id"]].get("source") != "review"]
        self.projects = [c for c in links._catalogue(notes_dir) if recs[c["id"]].get("project_meta")]
        self.by_recent = sorted(self.cat, key=lambda c: recs[c["id"]].get("created") or "", reverse=True)
        self.last_id = last_id or (self.by_recent[0]["id"] if self.by_recent else None)
        self.named = []          # everything referred to so far, in order

    def item(self, rid):
        return next((c for c in self.cat if c["id"] == rid), None)


def resolve_ref(part, ctx):
    """-> ("one", item) | ("group", [items]) | ("many", [items]) | ("none", None), tier"""
    p = re.sub(r"\s+", " ", part.strip().strip("\"'“”‘’.,").strip()).lower()
    p = re.sub(r"^(?:in\s+)?(?:with\s+)?", "", p)
    p = re.sub(r"\s+together$", "", p)
    if PRON_RE.match(p):
        it = ctx.item(ctx.last_id)
        return ("one", it, 5) if it else ("none", None, 0)
    m = OTHERS_RE.match(p)
    if m:
        taken = {c["id"] for c in ctx.named} | {ctx.last_id}
        others = [c for c in ctx.by_recent if c["id"] not in taken]
        n = _num(m.group("n") or m.group("n2")) or (2 if p.startswith("both") else None)
        if n is None:
            return ("group", others, 5) if 0 < len(others) <= 5 else ("none", None, 0)
        return ("group", others[:n], 5) if len(others) >= n else ("none", None, 0)
    if ALL_RE.match(p):
        return ("group", list(ctx.named), 5) if ctx.named else ("none", None, 0)
    return links.resolve(part, ctx.cat)


def resolve_list(text, ctx, allow_single=False):
    """Split `text` into note references. Returns (items, problem_line)."""
    text = TRAIL_RE.sub("", FILLER_RE.sub("", text.strip())).strip()
    if not text:
        return [], None
    options = [[text]] + list(links._partitions(text))
    best, best_score, first_problem = None, None, None
    for parts in options:
        res = [resolve_ref(p, ctx) for p in parts]
        if all(r[0] in ("one", "group") for r in res):
            items = []
            for r in res:
                for it in ([r[1]] if r[0] == "one" else r[1]):
                    if it["id"] not in {x["id"] for x in items}:
                        items.append(it)
            if len(items) < (1 if allow_single else 2):
                continue
            score = (sum(r[2] for r in res), -len(parts))
            if best_score is None or score > best_score:
                best, best_score = items, score
        elif first_problem is None and len(parts) > 1 or (first_problem is None and len(options) == 1):
            first_problem = (parts, res)
    if best:
        return best, None
    if first_problem:
        parts, res = first_problem
        for p, r in zip(parts, res):
            if r[0] == "many":
                return [], f"'{p}' could be " + " or ".join(f"'{h['title']}'" for h in r[1][:4]) + ", sir. Which one?"
        missing = [p for p, r in zip(parts, res) if r[0] == "none"]
        if missing:
            return [], "I can't find a note called " + " or ".join(f"'{m}'" for m in missing) + ", sir."
    return [], "I couldn't tell which notes you mean, sir."


def parse(text, ctx):
    """-> {"link": [items] | None, "file": {"notes": [...], "name": str} | None, "problem": str | None}"""
    t = text.strip().rstrip(".!")
    t = re.sub(r"[\s,]+please$", "", t, flags=re.I)
    plan = {"link": None, "file": None, "unlink": False, "problem": None}
    fm = None
    for m in FILE_RE.finditer(t):
        fm = m
    if fm and re.fullmatch(r"put|move[d]?|add(?:ed)?|place[d]?|sort(?:ed)?", fm.group("verb"), re.I):
        # everyday verbs ("put it in simple terms", "add that to the list") only
        # count as filing when a project/folder is named, or the name is an existing project
        named_proj = re.search(r"\b(project|folder|category|collection)\b", fm.group(0), re.I)
        nm = _norm_name(fm.group("name"))
        if not named_proj and not any(_norm_name(p["title"]) == nm for p in ctx.projects):
            fm = None
    link_text = t
    if fm:
        name = fm.group("name").strip().strip("\"'“”‘’").strip()
        name = re.sub(r"\s+(?:please|for me|thanks|thank you)$", "", name, flags=re.I).strip()
        name = re.sub(r"\s+(?:project|folder|category|group|section|collection)$", "", name, flags=re.I).strip()
        what = fm.group("what").strip()
        before = t[:fm.start()]
        if not what:           # passive: "... and then all 3 filed as X"
            tail = re.search(r"(?:^|\s)((?:all\s+(?:\d+|two|three|four|five|of\s+them)|them(?:\s+all)?|these|those|both|"
                             r"it|that|this|everything|the\s+lot)(?:\s+(?:be|to\s+be|get|got))?)\s*$", before, re.I)
            if tail:
                what = re.sub(r"\s+(?:be|to\s+be|get|got)$", "", tail.group(1), flags=re.I)
                before = before[:tail.start()]
        link_text = before
        plan["file"] = {"what": what or "that", "name": name,
                        # 3.8.0: "file ... as X" makes a category; say "project" for a project
                        "kind": "project" if re.search(r"\bproject\b", fm.group(0), re.I) else "category"}
    lm = re.search(rf"\b(unlink(?:ed|ing)?|disconnect(?:ed)?|{LINK_VERB})\b", link_text, re.I)
    if lm:
        plan["unlink"] = lm.group(1).lower().startswith(("un", "dis"))
        subject = link_text[:lm.start()]
        rest = link_text[lm.end():]
        rest = re.sub(r"^\s*(?:it|that|this|them)?\s*(?:in|up|together)?\s*(?:with|to|and|onto)?\s+", " ", rest, flags=re.I)
        subj = re.sub(r"\b(?:i\s+want|i'?d\s+like|can\s+you|could\s+you|please|jarvis|have|get|to\s+be|be)\b", " ", subject, flags=re.I)
        subj = TRAIL_RE.sub("", FILLER_RE.sub("", subj.strip())).strip()
        # "link that in with X": the thing before/after the verb
        obj_head = re.match(r"^\s*(it|that|this|them)\b", link_text[lm.end():], re.I)
        pieces = [x for x in (subj, obj_head.group(1) if obj_head and not subj else "", rest) if x and x.strip()]
        items, problem = resolve_list(" and ".join(p.strip() for p in pieces), ctx)
        if problem:
            plan["problem"] = problem
            return plan
        plan["link"] = items
        ctx.named = list(items)
    if plan["file"]:
        items, problem = resolve_list(plan["file"]["what"], ctx, allow_single=True)
        if problem:
            plan["problem"] = problem
            return plan
        plan["file"]["notes"] = items
    if not plan["link"] and not plan["file"]:
        plan["problem"] = "unparsed"
    return plan


def _norm_name(s):
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def file_under(name, items, notes_dir=None, kind="category"):
    """Files notes under the category (or project) `name`, made if new; an
    existing category or project of that name is always reused. A note is
    in one place at a time: filing it again moves it. Returns (title,
    created?, id, kind)."""
    name = name.strip()
    name = name[0].upper() + name[1:] if name else name
    recs, _ = records.load_all(notes_dir)
    proj = next((sc for sc in recs.values() if sc.get("project_meta") and not sc.get("missing")
                 and _norm_name(reviews._title(sc, notes_dir)) == _norm_name(name)), None)
    created = proj is None
    if created:
        proj = loops.create_project(name, [], notes_dir=notes_dir, kind=kind)
    kind = (proj.get("project_meta") or {}).get("kind") or "project"
    with records._LOCK:
        recs, _ = records.load_all(notes_dir)
        proj = recs[proj["id"]]
        for it in items:
            m = recs.get(it["id"])
            if not m:
                continue
            old = m.get("filed_under")
            if old and old != proj["id"] and old in recs:          # moving from another category
                for x, y in ((m, recs[old]), (recs[old], m)):
                    x["links"] = [l for l in x.get("links", []) if not (l.get("target_id") == y["id"] and l.get("reason") == "filed together")]
                records.save(recs[old], notes_dir)
            m["status"], m["filed_under"] = "sorted", proj["id"]
            if kind == "project":
                m["home"] = "PROJECTS"
                if m.get("loop"):
                    m["loop"]["project"] = proj["id"]
            for x, y in ((m, proj), (proj, m)):
                if y["id"] in x.get("unlinked", []):
                    x["unlinked"].remove(y["id"])
                if not links._link_between(x, y["id"]):
                    x.setdefault("links", []).append({"kind": "related", "target_id": y["id"], "target_path": y["path"],
                                                     "by": "you", "confirmed": True, "reason": "filed together",
                                                     "at": records.now_iso()})
            records._event(m, "filed by you", under=name)
            records.save(m, notes_dir)
        records._event(proj, "notes filed here by you", count=len(items))
        records.save(proj, notes_dir)
    return reviews._title(proj, notes_dir), created, proj["id"], kind


def unfile(items, notes_dir=None):
    """Takes notes out of whatever category/project they're filed under."""
    with records._LOCK:
        recs, _ = records.load_all(notes_dir)
        n = 0
        for it in items:
            m = recs.get(it["id"])
            old = m and m.get("filed_under")
            if not old:
                continue
            m.pop("filed_under")
            if old in recs:
                for x, y in ((m, recs[old]), (recs[old], m)):
                    x["links"] = [l for l in x.get("links", []) if not (l.get("target_id") == y["id"] and l.get("reason") == "filed together")]
                records.save(recs[old], notes_dir)
            if (m.get("loop") or {}).get("project") == old:
                m["loop"]["project"] = None
            records._event(m, "taken out of its category by you")
            records.save(m, notes_dir)
            n += 1
        return n


def _names(items):
    q = [f"'{i['title']}'" for i in items]
    return q[0] if len(q) == 1 else ", ".join(q[:-1]) + " and " + q[-1]


WORDS = {1: "it", 2: "both", 3: "all three", 4: "all four", 5: "all five"}


def execute(plan, ctx, saved=None):
    """Carries out a parsed plan; returns the spoken line."""
    said = []
    if saved:
        said.append(f"Saved '{saved}'")
    if plan["link"]:
        if plan["unlink"]:
            links.apply("unlink", plan["link"], ctx.notes_dir)
            said.append(f"unlinked {_names(plan['link'])}")
        else:
            links.apply("link", plan["link"], ctx.notes_dir)
            said.append(f"linked {_names(plan['link'])}")
    if plan["file"]:
        items = plan["file"]["notes"]
        title, created, _pid, kind = file_under(plan["file"]["name"], items, ctx.notes_dir, plan["file"].get("kind", "category"))
        who = WORDS.get(len(items), f"all {len(items)}") if plan["link"] and set(i["id"] for i in items) == set(
            i["id"] for i in plan["link"]) else _names(items)
        said.append(f"filed {who} under {'a new ' + kind + ', ' if created else ''}'{title}'")
    if not said:
        return None
    said[0] = said[0][0].upper() + said[0][1:]
    line = said[0] if len(said) == 1 else ", ".join(said[:-1]) + (", and " if len(said) > 2 else " and ") + said[-1]
    return line + ", sir."


# ---- the AI as an interpreter only (never an actor) ---------------------------
# (Since the merge, server.py hands sentences this parser can't place to
# actions.py instead, which also sees the recent conversation and offers
# Undo. This simpler interpreter stays for direct use and its tests.)

BRAIN_PROMPT = """You turn one instruction about organising notes into JSON. You do not act.
Notes are numbered below. "last" is the note the user most recently made.
Reply with ONLY a JSON object:
{"link": [numbers of notes to link together] or [],
 "unlink": true/false,
 "file_notes": [numbers of notes to file] or [],
 "file_under": "the project/folder name EXACTLY as the user wrote it" or ""}
If the instruction is not about linking or filing notes, reply {"none": true}."""


def interpret_with_brain(text, ctx, call_brain, config):
    listing = [f"{n}. {c['title']}" + (" (last)" if c["id"] == ctx.last_id else "")
               for n, c in enumerate(ctx.by_recent[:60], 1)]
    msgs = [{"role": "system", "content": BRAIN_PROMPT},
            {"role": "user", "content": "Notes:\n" + "\n".join(listing) + f"\n\nInstruction: {text}"}]
    try:
        reply, _model, _ = call_brain(config, msgs)
        m = re.search(r"\{.*\}", reply or "", re.S)
        data = json.loads(m.group(0)) if m else {}
    except Exception:
        return None
    if data.get("none"):
        return None
    pick = lambda nums: [ctx.by_recent[n - 1] for n in nums if isinstance(n, int) and 1 <= n <= min(60, len(ctx.by_recent))]
    link_items = pick(data.get("link") or [])
    file_items = pick(data.get("file_notes") or [])
    name = str(data.get("file_under") or "").strip()
    # CHECK: the name must be the user's own words, or an existing project
    if name and _norm_name(name) not in _norm_name(text) and \
            not any(_norm_name(p["title"]) == _norm_name(name) for p in ctx.projects):
        name = ""
    plan = {"link": link_items if len(link_items) >= 2 else None, "unlink": bool(data.get("unlink")),
            "file": {"notes": file_items, "name": name,
                     "kind": "project" if re.search(r"\bproject\b", text, re.I) else "category"} if file_items and name else None,
            "problem": None}
    return plan if plan["link"] or plan["file"] else None


def handle(text, notes_dir=None, previous_text=None, capture=None, call_brain=None, config=None, focus_id=None):
    """The whole request. `capture(text)` saves an unsaved previous message and
    returns its record id. Returns {"handled", "spoken", "ok"}."""
    if not is_organise(text):
        # 3.8.0: "file dave birthday and image reading as Personal stuff" -- no
        # "note"/"that" word, but every part names a real note: that's filing.
        if re.search(r"\b(file[d]?|filing)\b.*\b(as|under|in|into)\b", text, re.I):
            trial = parse(text, Ctx(notes_dir, last_id=focus_id))
            if trial.get("file") and trial["file"].get("notes") and not trial.get("problem"):
                return {"handled": True, "ok": True, "spoken": execute(trial, Ctx(notes_dir, last_id=focus_id))}
        return {"handled": False}
    # 3.8.0: "that" = the last thing discussed: a star you just clicked or
    # were shown (focus_id), else your last unsaved message, else the newest note.
    ctx = Ctx(notes_dir, last_id=focus_id)
    refers_back = re.search(r"\b(that|this|it)\b", text, re.I)
    saved = None
    if previous_text and refers_back and capture and not focus_id:
        rid = capture(previous_text)
        if rid:
            ctx = Ctx(notes_dir, last_id=rid)
            saved = (ctx.item(rid) or {}).get("title")
    plan = parse(text, ctx)
    # Only speak up about a problem when this clearly was about notes;
    # "I want to file my taxes as soon as possible" goes to chat as normal.
    about_notes = re.search(r"\b(notes?|stars?|projects?|folders?|under)\b", text, re.I) or \
        re.search(rf"\b{LINK_VERB}\b.*\b(that|it|them|these|those)\b|\b(that|it|them|these|those)\b.*\b{LINK_VERB}\b", text, re.I)
    if plan.get("problem") and plan["problem"] != "unparsed" and not about_notes:
        plan["problem"] = "unparsed"
    if plan["problem"] == "unparsed" and call_brain and config is not None and about_notes:
        ctx.named = []
        plan = interpret_with_brain(text, ctx, call_brain, config) or plan
    if plan.get("problem") and plan["problem"] != "unparsed":
        return {"handled": True, "ok": False, "spoken": (f"Saved '{saved}'. But " if saved else "") + plan["problem"]}
    if plan.get("problem") == "unparsed":
        if saved:
            return {"handled": True, "ok": True, "spoken": f"Saved '{saved}', sir. I couldn't work out the rest; "
                                                           "try: link that with <note> and file them under <name>."}
        return {"handled": False}
    return {"handled": True, "ok": True, "spoken": execute(plan, ctx, saved)}
