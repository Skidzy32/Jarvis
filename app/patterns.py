"""
patterns.py — Personal OS Phase 7 (3.5.0): pattern detection (spec §17).

Observations, never speculation. Every pattern:
  - is a plain statement of what the records show ("mentioned in 4 notes
    on 4 different days"), never a reason or a diagnosis;
  - carries its evidence (the notes, or for usage the report weeks) so you
    can check it yourself;
  - has its own minimum, and nothing at all is offered until there's
    enough dated history overall (MIN_NOTES over MIN_SPAN_DAYS). Below
    that Jarvis says so, with the numbers.

Runs entirely here: no brain call, so nothing from your notes or the usage
reports leaves the machine for this (follow-up F1 still stands for
anything that would).

Detectors:
  mentions  a person, place, organisation or theme in 3+ notes on 3+ days
  rising    a name/theme noticeably more frequent in the last 30 days than
            the 30 before (only once there are 60+ days of history)
  feelings  the same feeling word in 3+ journal/reflection notes, and the
            weekday if most of them share one
  problems  the same thing noted as a problem 2+ times
  slipping  a loop whose date you've moved 2+ times
  stalled   2+ active projects untouched for 30+ days
  usage     focus-session time across 3+ weekly usage reports (counts only)

Standard library only.
"""

import datetime
import glob
import json
import os
import re

import records
import reviews

MIN_NOTES = 20
MIN_SPAN_DAYS = 14
MENTION_NOTES = 3
MENTION_DAYS = 3
RISING_WINDOW = 30
RISING_MIN = 3
FEELING_NOTES = 3
PROBLEM_NOTES = 2
SLIP_MOVES = 2
STALL_DAYS = 30
STALL_PROJECTS = 2
USAGE_WEEKS = 3
EVIDENCE_MAX = 8

FEELINGS = ("behind", "stressed", "tired", "exhausted", "anxious", "overwhelmed", "stuck", "chaotic",
            "frustrated", "lonely", "bored", "happy", "calm", "proud", "excited", "grateful", "motivated",
            "drained", "worried", "rushed")
REFLECTIVE_KINDS = {"reflection", "experience"}
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _date(iso):
    try:
        return datetime.date.fromisoformat((iso or "")[:10])
    except ValueError:
        return None


def _fmt(d):
    return f"{d.day} {d.strftime('%b')}"


def _notes(notes_dir, today):
    recs, _ = records.load_all(notes_dir)
    out = []
    for sc in recs.values():
        d = _date(sc.get("created"))
        if sc.get("missing") or sc.get("status") != "sorted" or not d or d > today:
            continue
        if sc.get("source") == "review" or sc.get("project_meta"):   # Jarvis's own review entries and project files
            continue
        out.append({"id": sc["id"], "sc": sc, "date": d, "v": reviews._reading(sc), "home": sc.get("home"),
                    "title": reviews._title(sc, notes_dir)})
    return out


def _ev(items):
    items = sorted(items, key=lambda i: i["date"])
    return [{"id": i["id"], "title": i["title"], "date": i["date"].isoformat()} for i in items[:EVIDENCE_MAX]]


def _names(n):
    """Every name/theme a note's checked reading mentions, keyed lower-case."""
    v = n["v"]
    out = {}
    for field in ("people", "places", "organisations", "themes"):
        for x in v.get(field, []) or []:
            if isinstance(x, str) and x.strip():
                out.setdefault(x.strip().lower(), x.strip())
    return out


def readiness(items, today):
    span = (today - min(i["date"] for i in items)).days if items else 0
    ready = len(items) >= MIN_NOTES and span >= MIN_SPAN_DAYS
    return {"ready": ready, "notes": len(items), "span_days": span,
            "needs": {"notes": MIN_NOTES, "span_days": MIN_SPAN_DAYS}}


def mentions(items):
    groups = {}
    for n in items:
        for k, name in _names(n).items():
            groups.setdefault(k, {"name": name, "items": []})["items"].append(n)
    out = []
    for g in groups.values():
        days = {i["date"] for i in g["items"]}
        if len(g["items"]) >= MENTION_NOTES and len(days) >= MENTION_DAYS:
            first = min(days)
            out.append({"kind": "mentions", "weight": len(g["items"]), "evidence": _ev(g["items"]),
                        "line": f"You've mentioned {g['name']} in {len(g['items'])} notes on {len(days)} "
                                f"different days, first on {_fmt(first)}."})
    return sorted(out, key=lambda p: -p["weight"])


def rising(items, today):
    if not items or (today - min(i["date"] for i in items)).days < 2 * RISING_WINDOW:
        return []
    recent, before = {}, {}
    names = {}
    for n in items:
        age = (today - n["date"]).days
        bucket = recent if age < RISING_WINDOW else before if age < 2 * RISING_WINDOW else None
        if bucket is None:
            continue
        for k, name in _names(n).items():
            names.setdefault(k, name)
            bucket.setdefault(k, []).append(n)
    out = []
    for k, now_items in recent.items():
        was = len(before.get(k, []))
        if len(now_items) >= RISING_MIN and len(now_items) >= 2 * max(was, 1):
            out.append({"kind": "rising", "weight": len(now_items) - was, "evidence": _ev(now_items),
                        "line": f"{names[k]} is coming up more often: {was} note{'s' if was != 1 else ''} in the "
                                f"30 days before, {len(now_items)} in the last 30."})
    return sorted(out, key=lambda p: -p["weight"])


def feelings(items, notes_dir=None):
    import sorting
    groups = {}
    for n in items:
        if not (n["home"] == "JOURNAL" or REFLECTIVE_KINDS & set(n["v"].get("kinds", []))):
            continue
        try:
            words = set(re.findall(r"[a-z]+", sorting.note_words(n["sc"]["path"], notes_dir).lower()))
        except OSError:
            continue
        for f in FEELINGS:
            if f in words:
                groups.setdefault(f, []).append(n)
    out = []
    for word, its in groups.items():
        if len(its) < FEELING_NOTES:
            continue
        line = f"You've written '{word}' in {len(its)} reflections."
        by_day = {}
        for i in its:
            by_day.setdefault(i["date"].weekday(), []).append(i)
        wd, same = max(by_day.items(), key=lambda kv: len(kv[1]))
        if len(same) >= 3 and len(same) / len(its) >= 0.6:
            line += f" {len(same)} of them on a {WEEKDAYS[wd]}."
        out.append({"kind": "feelings", "weight": len(its), "evidence": _ev(its), "line": line})
    return sorted(out, key=lambda p: -p["weight"])


def problems(items):
    groups = {}
    for n in items:
        if "problem" not in n["v"].get("kinds", []):
            continue
        for k, name in _names(n).items():
            groups.setdefault(k, {"name": name, "items": []})["items"].append(n)
    return sorted([{"kind": "problems", "weight": len(g["items"]), "evidence": _ev(g["items"]),
                    "line": f"You've noted {g['name']} as a problem {len(g['items'])} times."}
                   for g in groups.values() if len(g["items"]) >= PROBLEM_NOTES], key=lambda p: -p["weight"])


def slipping(items):
    out = []
    for n in items:
        loop = n["sc"].get("loop") or {}
        moves = [h for h in n["sc"].get("history", []) if h.get("event") == "loop due"]
        if loop.get("state") == "open" and len(moves) >= SLIP_MOVES:
            out.append({"kind": "slipping", "weight": len(moves), "evidence": _ev([n]),
                        "line": f"'{n['title']}' has had its date moved {len(moves)} times"
                                + (f"; it's now {_fmt(_date(loop['due']))}." if _date(loop.get("due")) else ".")})
    return sorted(out, key=lambda p: -p["weight"])


def stalled(notes_dir, today):
    recs, _ = records.load_all(notes_dir)
    quiet = []
    for sc in recs.values():
        meta = sc.get("project_meta") or {}
        if meta.get("kind") == "category":
            continue
        if sc.get("missing") or not meta or meta.get("status", "active") != "active":
            continue
        last = max([_date(h.get("at")) for h in sc.get("history", []) if _date(h.get("at"))] or [_date(sc.get("created"))],
                   default=None)
        if last and (today - last).days >= STALL_DAYS:
            quiet.append({"id": sc["id"], "title": reviews._title(sc, notes_dir), "date": last})
    if len(quiet) < STALL_PROJECTS:
        return []
    return [{"kind": "stalled", "weight": len(quiet), "evidence": _ev(quiet),
             "line": f"{len(quiet)} active projects have gone quiet for a month or more: "
                     + ", ".join(f"'{q['title']}' (last touched {_fmt(q['date'])})" for q in quiet[:3]) + "."}]


def usage(usage_dir=None):
    """Counts only, read locally from the weekly usage reports."""
    if usage_dir is None:
        import usage_tracker
        usage_dir = usage_tracker.USAGE_DIR
    weeks = []
    for path in sorted(glob.glob(os.path.join(usage_dir, "reports", "weekly", "*.json")))[-6:]:
        try:
            with open(path, encoding="utf-8") as f:
                r = json.load(f)
        except (OSError, ValueError):
            continue
        weeks.append((os.path.splitext(os.path.basename(path))[0], r))
    if len(weeks) < USAGE_WEEKS:
        return []
    hours = lambda s: f"{s / 3600:.1f}h"
    ev = [{"id": None, "title": f"usage report {w}", "date": w} for w, _ in weeks]
    out = [{"kind": "usage", "weight": len(weeks), "evidence": ev,
            "line": f"Focus-session time over the last {len(weeks)} weeks: "
                    + ", ".join(hours(r.get("focus_seconds", 0)) for _, r in weeks)
                    + f" (of {', '.join(hours(r.get('tracked_seconds', 0)) for _, r in weeks)} tracked)."}]
    tops = []
    for _, r in weeks:
        e = sorted((r.get("entries") or {}).values(), key=lambda x: -x.get("seconds", 0))
        tops.append(e[0]["label"] if e else None)
    top = max(set(t for t in tops if t), key=tops.count, default=None)
    if top and tops.count(top) >= USAGE_WEEKS:
        out.append({"kind": "usage", "weight": tops.count(top), "evidence": ev,
                    "line": f"{top} took the most time in {tops.count(top)} of the last {len(weeks)} weeks."})
    return out


def detect(notes_dir=None, today=None, usage_dir=None):
    """{"ready", "readiness", "patterns": [...], "spoken"}"""
    today = today or datetime.date.today()
    items = _notes(notes_dir, today)
    rd = readiness(items, today)
    found = []
    if rd["ready"]:
        found = (problems(items) + slipping(items) + feelings(items, notes_dir) + rising(items, today)
                 + mentions(items) + stalled(notes_dir, today))
    use = usage(usage_dir)          # has its own minimum (3 weekly reports), independent of notes
    found += use
    return {"ready": rd["ready"], "readiness": rd, "patterns": found, "spoken": spoken(rd, found)}


def not_enough_line(rd):
    return (f"Not enough yet to call anything a pattern, sir: {rd['notes']} sorted "
            f"note{'s' if rd['notes'] != 1 else ''} over {rd['span_days']} day{'s' if rd['span_days'] != 1 else ''}. "
            f"I start looking at about {MIN_NOTES} notes across {MIN_SPAN_DAYS} days.")


def spoken(rd, found):
    if not found:
        return not_enough_line(rd) if not rd["ready"] else \
            "Nothing stands out as a pattern yet, sir. I'll keep an eye out."
    line = "What the records show: " + " ".join(p["line"] for p in found[:3])
    if not rd["ready"]:
        line += (f" Your notes don't have enough history for patterns yet ({rd['notes']} over "
                 f"{rd['span_days']} days; I need about {MIN_NOTES} across {MIN_SPAN_DAYS}).")
    return line + " The evidence for each is listed."
