"""
wonder.py — Personal OS Phase 8 (2.9.0): wonder, anticipation, agency.

Deliberately not productivity (spec §18-20). Nothing here makes a task,
a project or a reminder out of an interest. It only helps keep curiosity,
things to look forward to, and the things you do because you want to,
visible next to everything you have to do.

  - Looking forward to (§19): this week / this month / this year, from
    dates actually written in your notes, plus undated wishes ("whenever
    you like"). Trips, events, experiences, things to learn or build.
  - Balance (§20): of what's open and recent, how much is obligation or
    responsibility, and how much is choice, desire or curiosity (the
    "nature" sorting now reads for each note). Only said gently, and only
    when obligations clearly dominate.
  - A wonder line (§18) at most once a week in the evening review: an
    interest you've kept coming back to, or a wish untouched for months.
  - The agency question (§20), "What's something you want to do simply
    because you want to?", in the weekly review every other week.

Standard library only; everything comes from your notes and their records.
"""

import datetime

import records
import reviews

ANTICIPATION_KINDS = {"experience", "future_possibility", "wonder", "goal", "idea"}
OBLIGATORY = ("obligation", "responsibility")
CHOSEN = ("choice", "desire", "curiosity")
UNTOUCHED_DAYS = 90
AGENCY_QUESTION = "What's something you want to do simply because you want to?"


def _age(iso, today):
    return reviews._days_since(iso, today)


def _items(notes_dir, today):
    recs, _ = records.load_all(notes_dir)
    out = []
    for sc in recs.values():
        if sc.get("missing") or sc.get("status") != "sorted":
            continue
        v = reviews._reading(sc)
        out.append({"id": sc["id"], "title": reviews._title(sc, notes_dir), "home": sc.get("home"), "v": v,
                    "loop": sc.get("loop"), "created": sc.get("created"),
                    "last": max([h.get("at", "") for h in sc.get("history", [])] or [""]),
                    "age": _age(sc.get("created"), today)})
    return out


def is_anticipation(i):
    return i["home"] == "WONDER" or bool(ANTICIPATION_KINDS & set(i["v"].get("kinds", []))) \
        or i["v"].get("nature") in ("desire", "curiosity")


def horizons(notes_dir=None, today=None):
    """{"this week": [...], "this month": [...], "this year": [...], "whenever": [...]}
    by the first future date written in the note."""
    today = today or datetime.date.today()
    t = today.isoformat()
    out = {"this week": [], "this month": [], "this year": [], "whenever": []}
    for i in _items(notes_dir, today):
        dates = sorted(d["iso"] for d in i["v"].get("dates", []) if d.get("iso") and d["iso"] >= t)
        is_event = any(d.get("kind") == "event" for d in i["v"].get("dates", []))
        if not (is_anticipation(i) or (is_event and i["v"].get("nature") not in OBLIGATORY)):
            continue
        if (i["loop"] or {}).get("state") in ("done", "dropped"):
            continue
        if not dates:
            out["whenever"].append(i)
            continue
        days = (datetime.date.fromisoformat(dates[0]) - today).days
        key = "this week" if days <= 7 else "this month" if days <= 31 else "this year" if days <= 365 else "whenever"
        out[key].append(dict(i, when=dates[0]))
    for k in ("this week", "this month", "this year"):
        out[k].sort(key=lambda i: i["when"])
    return out


def balance(notes_dir=None, today=None, days=30):
    """Counts of what's open or recent, by nature. Notes the reading didn't
    classify are left out rather than guessed."""
    today = today or datetime.date.today()
    counts = {n: 0 for n in OBLIGATORY + CHOSEN}
    for i in _items(notes_dir, today):
        open_loop = (i["loop"] or {}).get("state") == "open"
        recent = i["age"] is not None and i["age"] <= days
        nature = i["v"].get("nature")
        if nature in counts and (open_loop or recent):
            counts[nature] += 1
    must = sum(counts[n] for n in OBLIGATORY)
    want = sum(counts[n] for n in CHOSEN)
    total = must + want
    line = None
    if total >= 4:
        if must >= 3 * max(want, 1) and must / total >= 0.75:
            line = (f"Of the {total} things I can place, {must} are obligations or responsibilities and "
                    f"{want} {'is' if want == 1 else 'are'} something you chose or want. "
                    f"{AGENCY_QUESTION}")
        else:
            line = (f"A fair mix: {must} obligation{'s' if must != 1 else ''} or responsibilities, "
                    f"{want} thing{'s' if want != 1 else ''} you chose, want or are curious about.")
    return {"counts": counts, "must": must, "want": want, "line": line}


def threads(notes_dir=None, today=None):
    """Curiosity threads: a theme, place or person shared by 3+ Wonder /
    curiosity notes that haven't become anything."""
    today = today or datetime.date.today()
    groups = {}
    for i in _items(notes_dir, today):
        if not (i["home"] == "WONDER" or i["v"].get("nature") == "curiosity"):
            continue
        for key in {k.lower(): k for k in i["v"].get("themes", []) + i["v"].get("places", [])}.items():
            groups.setdefault(key[0], {"name": key[1], "items": []})["items"].append(i)
    return sorted([g for g in groups.values() if len(g["items"]) >= 3], key=lambda g: -len(g["items"]))


def wonder_line(notes_dir=None, today=None):
    """One gentle line, or None: a curiosity thread, else a wish untouched
    for months, else the most recent interest."""
    today = today or datetime.date.today()
    th = threads(notes_dir, today)
    if th:
        g = th[0]
        return (f"You have {len(g['items'])} curiosity threads around {g['name']}: "
                + ", ".join(f"'{i['title']}'" for i in g["items"][:3]) + ". No action needed; just in case it sparks something.")
    items = [i for i in _items(notes_dir, today) if is_anticipation(i)]
    old = sorted([i for i in items if (_age(i["last"], today) or 0) >= UNTOUCHED_DAYS], key=lambda i: i["last"])
    if old:
        i = old[0]
        months = max(1, _age(i["last"], today) // 30)
        return f"You once said: '{i['title']}'. That was about {months} month{'s' if months != 1 else ''} ago. Still appealing?"
    recent = sorted([i for i in items if (i["age"] if i["age"] is not None else 99) <= 21], key=lambda i: i["created"] or "", reverse=True)
    if recent:
        return f"Something you've been curious about lately: '{recent[0]['title']}'."
    return None


def weekly_wonder_due(state, today):
    key = f"{today.isocalendar()[0]}-W{today.isocalendar()[1]:02d}"
    return state.get("wonder_week") != key, key


def agency_week(today):
    """The agency question comes every other week."""
    return today.isocalendar()[1] % 2 == 0


def spoken_horizon(h, which=None):
    keys = [which] if which else ["this week", "this month", "this year"]
    parts = []
    for k in keys:
        if h.get(k):
            parts.append(f"{k}: " + ", ".join(f"'{i['title']}'" for i in h[k][:3]))
    if not parts:
        wish = h.get("whenever", [])
        if wish:
            return ("Nothing dated to look forward to" + (f" {which}" if which else "") +
                    f", sir, but there's '{wish[0]['title']}' whenever you fancy it.")
        return "Nothing noted to look forward to yet, sir. Worth adding something you'd enjoy?"
    line = "To look forward to, " + "; ".join(parts) + "."
    if h.get("whenever") and not which:
        line += " And whenever you fancy it: " + ", ".join(f"'{i['title']}'" for i in h["whenever"][:2]) + "."
    return line
