"""
links.py — 3.6.0: "link X and Y" / "unlink X and Y", done here, not by the AI.

Before this there was no linking command at all: the request went to the
free chat model, which can only talk, so it hedged ("I need the exact
titles…") and nothing happened. Now:

  - Titles are matched here, forgivingly: exact title, then a title that
    contains what you said, then every word you said appearing in the title
    (so "iceland" finds "Iceland trip in March"). Only if two notes match
    equally well does Jarvis ask which one -- with the options.
  - "link A, B and C" links all of them to each other (up to 5 notes).
    Titles that themselves contain "and" still work: every way of splitting
    what you said is tried, and the one where every part names exactly one
    note wins.
  - Links go into the notes' records (by "you", confirmed). The notes
    themselves are never edited.
  - "unlink" removes the link and remembers the pair, so neither sorting
    nor the galaxy's automatic word-matching puts it back.

Standard library only.
"""

import itertools
import re

import records
import reviews

MAX_NOTES = 5
SEPARATORS = re.compile(r"\s*,\s*(?:and\s+)?|\s+(?:and|&|to|with|onto)\s+", re.I)
LEAD = re.compile(r"^(?:the\s+|my\s+)?(?:notes?|stars?)\s+(?:(?:called|named|titled)\s+)?|^(?:the|my)\s+", re.I)
TRAIL = re.compile(r"(?:\s+(?:notes?|stars?|ones?)|\s+together)+$", re.I)
STOP = {"the", "a", "an", "my", "of", "to", "and", "note", "notes", "star", "stars", "about", "on", "in", "for"}


def _norm(s):
    s = (s or "").strip().strip("\"'“”‘’.!?").strip()
    s = TRAIL.sub("", LEAD.sub("", s)).strip().strip("\"'“”‘’")
    return re.sub(r"\s+", " ", s.lower())


def _stem(w):
    w = re.sub(r"['’]s$", "", w).strip("'’")
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _words(s):
    """Content words, forgiving about plurals and possessives
    ("images" = "image", "OpenRouter's" = "openrouter")."""
    return {_stem(w) for w in re.findall(r"[a-z0-9'’]+", s.lower()) if w not in STOP} - {""}


def _catalogue(notes_dir):
    recs, _ = records.load_all(notes_dir)
    out = []
    for sc in recs.values():
        if sc.get("missing"):
            continue
        t = reviews._title(sc, notes_dir)
        out.append({"id": sc["id"], "title": t, "low": t.lower().strip(), "words": _words(t)})
    return out


def resolve(query, cat):
    """-> ("one", item) | ("many", [items]) | ("none", None), plus the tier."""
    q = _norm(query)
    if not q:
        return "none", None, 0
    qw = _words(q)
    tiers = [
        (4, [c for c in cat if c["low"] == q]),
        (3, [c for c in cat if c["low"].startswith(q)]),
        (2, [c for c in cat if q in c["low"]]),
        (1, [c for c in cat if qw and qw <= c["words"]]),
    ]
    for tier, hits in tiers:
        if len(hits) == 1:
            return "one", hits[0], tier
        if len(hits) > 1:
            return "many", hits, tier
    # Last resort: most of what you said is in one title ("openrouter privacy
    # settings note" -> "Check OpenRouter's privacy settings..."). At least two
    # words and two-thirds of them; only if one note clearly fits best.
    if len(qw) >= 2:
        scored = sorted(((len(qw & c["words"]), c) for c in cat), key=lambda x: -x[0])
        top = [c for n, c in scored if n == scored[0][0]] if scored else []
        if scored and scored[0][0] >= 2 and scored[0][0] * 3 >= len(qw) * 2:
            return ("one", top[0], 0.5) if len(top) == 1 else ("many", top, 0.5)
    return "none", None, 0


def _partitions(text):
    """Every way of cutting `text` at its separators into 2..MAX_NOTES parts."""
    cuts = [(m.start(), m.end()) for m in SEPARATORS.finditer(text)]
    for k in range(1, min(len(cuts), MAX_NOTES - 1) + 1):
        for chosen in itertools.combinations(cuts, k):
            parts, pos = [], 0
            for a, b in chosen:
                parts.append(text[pos:a])
                pos = b
            parts.append(text[pos:])
            if all(p.strip() for p in parts):
                yield [p.strip() for p in parts]


def understand(text, notes_dir=None):
    """Works out which notes `text` names. Returns
    {"ok": True, "notes": [items]} or {"ok": False, "spoken": why, "options": {...}}."""
    cat = _catalogue(notes_dir)
    best, best_score, best_partial = None, None, None
    for parts in _partitions(text):
        res = [resolve(p, cat) for p in parts]
        if all(r[0] == "one" for r in res):
            ids = [r[1]["id"] for r in res]
            if len(set(ids)) != len(ids):
                continue
            score = (sum(r[2] for r in res), -len(parts))   # best matches, then fewest cuts
            if best_score is None or score > best_score:
                best, best_score = [r[1] for r in res], score
        elif best_partial is None or sum(r[0] == "one" for r in res) > sum(r[0] == "one" for r in best_partial[1]):
            best_partial = (parts, res)
    if best:
        return {"ok": True, "notes": best}
    if best_partial is None:
        return {"ok": False, "spoken": "Which notes, sir? Say it like: link Iceland trip and packing list."}
    parts, res = best_partial
    missing = [p for p, r in zip(parts, res) if r[0] == "none"]
    many = [(p, r[1]) for p, r in zip(parts, res) if r[0] == "many"]
    if many:
        p, hits = many[0]
        opts = [h["title"] for h in hits[:4]]
        i = parts.index(p)
        retry = [" and ".join(parts[:i] + [o] + parts[i + 1:]) for o in opts]
        return {"ok": False, "options": opts, "retry": retry,
                "spoken": f"'{p}' could be " + ", ".join(f"'{o}'" for o in opts[:-1]) + f" or '{opts[-1]}', sir. Which one?"}
    return {"ok": False, "spoken": "I can't find a note called " + " or ".join(f"'{m}'" for m in missing)
            + ", sir. The title is the star's name in the galaxy; part of it is enough."}


def _link_between(x, y_id):
    return [l for l in x.get("links", []) if l.get("kind") == "related" and l.get("target_id") == y_id]


def apply(action, notes, notes_dir=None):
    """action "link" or "unlink" on every pair of `notes`. Returns spoken line."""
    if len(notes) < 2:
        raise ValueError("need at least two notes")
    done, already = 0, 0
    with records._LOCK:
        recs, _ = records.load_all(notes_dir)
        for a, b in itertools.combinations(notes, 2):
            x, y = recs.get(a["id"]), recs.get(b["id"])
            if not x or not y:
                continue
            changed = False
            for p, q in ((x, y), (y, x)):
                if action == "link":
                    if q["id"] in p.get("unlinked", []):
                        p["unlinked"].remove(q["id"]); changed = True
                    existing = _link_between(p, q["id"])
                    if existing:
                        for l in existing:
                            if not (l.get("by") == "you" and l.get("confirmed")):
                                l.update(by="you", confirmed=True, reason="linked by you"); changed = True
                    else:
                        p.setdefault("links", []).append({"kind": "related", "target_id": q["id"], "target_path": q["path"],
                                                         "by": "you", "confirmed": True, "reason": "linked by you",
                                                         "at": records.now_iso()})
                        changed = True
                else:
                    before = len(p.get("links", []))
                    p["links"] = [l for l in p.get("links", []) if not (l.get("kind") == "related" and l.get("target_id") == q["id"])]
                    if q["id"] not in p.setdefault("unlinked", []):
                        p["unlinked"].append(q["id"])
                        changed = True
                    changed = changed or len(p["links"]) != before
            if changed:
                for p, q in ((x, y), (y, x)):
                    records._event(p, "linked by you" if action == "link" else "unlinked by you", to=q["path"])
                    records.save(p, notes_dir)
                done += 1
            else:
                already += 1
    names = [f"'{n['title']}'" for n in notes]
    joined = ", ".join(names[:-1]) + " and " + names[-1]
    if action == "link":
        if not done:
            return f"{joined} are already linked, sir."
        return f"Linked {joined}, sir." if len(notes) == 2 else f"Linked {joined} to each other, sir."
    if not done:
        return f"{joined} weren't linked, sir. I'll keep them apart."
    return f"Unlinked {joined}, sir. I won't join them again unless you ask."


def blocked_pairs(notes_dir=None):
    """Pairs of record ids you've unlinked -- the galaxy and sorting skip them."""
    recs, _ = records.load_all(notes_dir)
    return {tuple(sorted((sc["id"], other))) for sc in recs.values() for other in sc.get("unlinked", [])}
