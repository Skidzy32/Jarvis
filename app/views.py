"""
views.py — Personal OS Phase 6 (2.8.0): asking Jarvis, and the overview.

"What is actually going on in my life right now?" (spec §12, §26) and a
work view (§11), plus answers to the spec's plain-English questions (§24):
"what am I putting off?", "what's coming up?", "what's worrying me?",
"what have I said I want to do?"...

Every answer comes straight from what's stored: your notes, their records,
loops and projects. No AI writes these answers, so nothing can be invented.
Each item names the note it came from. A question the router doesn't
recognise goes on to the usual brain (/chat), as before.

Standard library only.
"""

import datetime
import re

import loops
import records
import reviews
import wonder

RECENT_DAYS = 14
WEEK = 7


def _days(iso, today):
    return reviews._days_since(iso, today)


def gather(notes_dir=None, today=None):
    today = today or datetime.date.today()
    g = reviews.gather(notes_dir, today)
    recs, _ = records.load_all(notes_dir)
    g["notes"] = []
    for sc in recs.values():
        if sc.get("missing"):
            continue
        v = reviews._reading(sc)
        g["notes"].append({"id": sc["id"], "title": reviews._title(sc, notes_dir), "home": sc.get("home"),
                           "status": sc.get("status"), "created": sc.get("created"), "source": sc.get("source"),
                           "v": v, "loop": sc.get("loop"), "age": _days(sc.get("created"), today)})
    g["notes"].sort(key=lambda n: n["created"] or "", reverse=True)
    g["today"] = today
    return g


def _within(iso, today, days):
    d = _days(iso, today)
    return d is not None and d <= days


def _age(n):
    """Days since a note was captured. (0 = today: must not be read as
    "unknown" -- that bug hid today's notes from several answers.)"""
    return 99 if n["age"] is None else n["age"]


def _item(n, detail=None):
    return {"title": n["title"], "record_id": n["id"], "detail": detail}


def _due(n):
    return (n.get("loop") or {}).get("due")


def _fmt(iso):
    return reviews._fmt(iso)


# ---- the pieces every view and answer is made of ------------------------------

def top_today(g, k=3):
    """The 3 most important current items (spec §26), in the order spec §23
    gives: time-sensitive, then explicit commitments, then likely problems."""
    t = g["today"].isoformat()
    soon = (g["today"] + datetime.timedelta(days=2)).isoformat()
    def rank(i):
        d = _due(i)
        return (0 if d and d <= t else 1 if d and d <= soon else
                2 if "commitment" in i["v"].get("kinds", []) else
                3 if i["loop"]["kind"] == "to solve" else 4, d or "9", i["created"] or "")
    return sorted(g["open"], key=rank)[:k]


def upcoming(g, days=14):
    t, end = g["today"].isoformat(), (g["today"] + datetime.timedelta(days=days)).isoformat()
    out = [dict(i, when=_due(i)) for i in g["open"] if _due(i) and t <= _due(i) <= end]
    out += [e for e in g["events"] if e["when"] <= end]
    return sorted(out, key=lambda i: i["when"])


def putting_off(g):
    return sorted([i for i in g["open"] if i["loop"]["kind"] == "to do"
                   and (_days(i["loop"].get("opened_at"), g["today"]) or 0) >= WEEK],
                  key=lambda i: i["loop"].get("opened_at") or "")


def before_problem(g):
    t = g["today"].isoformat()
    soon = (g["today"] + datetime.timedelta(days=5)).isoformat()
    out = [i for i in g["open"] if _due(i) and _due(i) <= soon]
    out += [i for i in g["open"] if i["loop"]["kind"] == "to solve" and i not in out]
    out += [i for i in g["open"] if i["loop"]["kind"] == "waiting" and i not in out
            and (_days(i["loop"].get("opened_at"), g["today"]) or 0) >= reviews.STALE_WAITING_DAYS]
    return out


def wanted(g):
    """Things you said you'd like: Wonder and Someday, goals, possibilities."""
    return [n for n in g["notes"] if n["status"] == "sorted" and (
        n["home"] in ("WONDER", "SOMEDAY") or {"goal", "future_possibility"} & set(n["v"].get("kinds", [])))]


def worrying(g):
    """Problems and risks still open, and recent journal notes of how you've
    been feeling. Only what you wrote; no diagnosis."""
    out = [i for i in g["open"] if i["loop"]["kind"] == "to solve"]
    out += [n for n in g["notes"] if n["home"] == "JOURNAL" and _age(n) <= RECENT_DAYS
            and (n["v"].get("memory") == "current_state" or "reflection" in n["v"].get("kinds", []))]
    return out


def repeated(g):
    return sorted(((name, len({i["id"] for i in items})) for name, items in g["mentions"].items()
                   if len({i["id"] for i in items}) >= 3), key=lambda x: -x[1])


def recent(g, days=WEEK, home=None, kinds=None, memory=None):
    out = []
    for n in g["notes"]:
        if (n["age"] if n["age"] is not None else 99) > days or n["source"] == "file":
            continue
        if home and n["home"] != home:
            continue
        if kinds and not set(kinds) & set(n["v"].get("kinds", [])):
            continue
        if memory and n["v"].get("memory") != memory:
            continue
        out.append(n)
    return out


def decisions(g, days=30):
    return [n for n in g["notes"] if n["v"].get("decision") and _age(n) <= days]


def work(g):
    return [i for i in g["open"] if i["v"].get("area") == "Work"]


def areas_attention(g):
    counts = {}
    for i in g["open"]:
        a = i["v"].get("area")
        if a:
            c = counts.setdefault(a, [0, 0])
            c[0] += 1
            if _due(i) and _due(i) < g["today"].isoformat():
                c[1] += 1
    return sorted(counts.items(), key=lambda kv: (-kv[1][1], -kv[1][0]))


# ---- the overview (spec §26 / §12) and the work view (§11) ---------------------

def _sec(title, items, fmt=None):
    return {"title": title, "items": [
        {"text": fmt(i) if fmt else i["title"], "record_id": i.get("id")} for i in items]} if items else None


def overview(notes_dir=None, today=None):
    g = gather(notes_dir, today)
    t = g["today"]
    week_end = (t + datetime.timedelta(days=WEEK)).isoformat()
    waiting = [i for i in g["open"] if i["loop"]["kind"] == "waiting"]
    attention = ([{"title": f"{len(g['inbox'])} in the inbox, not yet sorted"}] if g["inbox"] else []) + \
                ([{"title": f"{len(g['needs_you'])} question(s) waiting for your word"}] if g["needs_you"] else []) + \
                [{"title": f"{a}: {c[0]} open" + (f", {c[1]} past their date" if c[1] else "")} for a, c in areas_attention(g)[:3]]
    forgotten = reviews.forgotten_sections(g, t, {})
    hz = wonder.horizons(notes_dir, t)
    bal = wonder.balance(notes_dir, t)
    import patterns
    pats = patterns.detect(notes_dir, t)
    life = [
        _sec("Today", top_today(g), lambda i: i["title"] + (f" · due {_fmt(_due(i))}" if _due(i) else "")),
        _sec("This week", [u for u in upcoming(g, WEEK)], lambda i: f"{i['title']} · {_fmt(i['when'])}"),
        _sec("Upcoming", [u for u in upcoming(g, 30) if u["when"] > week_end], lambda i: f"{i['title']} · {_fmt(i['when'])}"),
        _sec("Open loops", [{"title": f"{len(g['open'])} open: " + ", ".join(
            f"{v} {k}" for k, v in loops.overview(notes_dir, t)["counts"].items() if v)}] if g["open"] else []),
        _sec("Waiting for", waiting),
        _sec("Active projects", [p for p in g["projects"] if p["status"] == "active"]),
        _sec("Categories", loops.overview(notes_dir, t)["categories"], lambda c: f"{c['title']} · {c['count']} note{'s' if c['count'] != 1 else ''}"),
        _sec("Needs attention", attention),
        _sec("You may have forgotten", [{"title": s["items"][0]["text"], "id": s["items"][0].get("record_id")}
                                        for s in forgotten[:3]]),
        _sec("Recent decisions", decisions(g)[:3]),
        _sec("Recently captured", [n for n in g["notes"] if n["source"] not in ("file",)][:5]),
        _sec("Wonder", [n for n in g["notes"] if n["home"] == "WONDER"][:5]),
        # 2.9.0 (spec §19, §20)
        _sec("Looking forward to", [dict(i, key=k) for k in ("this week", "this month", "this year")
                                    for i in hz[k]][:6], lambda i: f"{i['title']} · {i['key']}"),
        _sec("Balance", [{"title": bal["line"]}] if bal["line"] else []),
        # 3.5.0 (spec §17): only once there's enough history; each line opens its first note
        _sec("Patterns", [{"title": p["line"], "id": (p["evidence"][0] or {}).get("id")}
                          for p in pats["patterns"][:4]]),
    ]
    w = work(g)
    work_view = [
        _sec("Current work priorities", top_today(dict(g, open=w))),
        _sec("Upcoming", [i for i in upcoming(dict(g, open=w, events=[e for e in g["events"]
                                                                      if e["v"].get("area") == "Work"]))],
             lambda i: f"{i['title']} · {_fmt(i['when'])}"),
        _sec("Waiting for", [i for i in w if i["loop"]["kind"] == "waiting"]),
        _sec("People", [{"title": p} for p in sorted({p for i in w for p in i["v"].get("people", [])})]),
        _sec("Problems", [i for i in w if i["loop"]["kind"] == "to solve"]),
        _sec("Follow-ups", [i for i in w if "commitment" in i["v"].get("kinds", [])]),
        _sec("Recent decisions", [n for n in decisions(g) if n["v"].get("area") == "Work"]),
        _sec("Things to watch", [i for i in before_problem(dict(g, open=w))]),
    ]
    return {"life": [s for s in life if s], "work": [s for s in work_view if s],
            "empty": not g["notes"] or not any(s for s in life if s)}


# ---- the question router (spec §24) ----------------------------------------------

def _list(items, fmt=lambda i: i["title"], n=4):
    names = [f"'{fmt(i)}'" for i in items[:n]]
    more = f", and {len(items) - n} more" if len(items) > n else ""
    return (", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else names[0]) + more if names else ""


def _ans(intent, spoken, items=()):
    return {"intent": intent, "spoken": spoken, "items": [
        {"title": i.get("title"), "record_id": i.get("id")} for i in items][:8]}


INTENTS = [  # (intent, pattern) -- checked in order, whole question
    ("today", r"what am i doing( today)?|what should i (be thinking about|focus on|do) today|what'?s (on )?today|what matters today"),
    ("putting_off", r"what (have i been|am i) putting off|what have i been avoiding|what am i procrastinating on"),
    ("projects", r"what projects am i (running|doing|working on)|what are my projects|which projects( are active)?"),
    ("decided", r"what did i decide (about|on) (?P<topic>.+)"),
    ("worrying", r"what('?s| is) (currently )?(worrying|bothering|stressing) me|what am i worried about"),
    ("wanted", r"what have i said i (want|wanted|would like) to do|what do i want to do|what'?s on my (wish|bucket) ?list"),
    ("repeated", r"what have i (mentioned|talked about|brought up) (a lot|repeatedly|over and over)"),
    ("coming_up", r"what('?s| is) coming up|what'?s ahead|what'?s next( week)?|anything coming up"),
    ("before_problem", r"what should i (probably )?deal with before it becomes a problem|what could become a problem|what might go wrong"),
    ("work", r"what'?s (happening|going on) at work|how'?s work( looking)?|work overview|work mode"),
    ("changed", r"what('?s| has) changed( recently| lately)?|what'?s new"),
    ("learned", r"what have i learn(ed|t)( recently| lately)?"),
    ("interested", r"what am i interested in( at the moment| right now| lately)?|what are my interests"),
    ("excited", r"what should i be excited about"),
    ("forward", r"what('?s| is) (there )?to look forward to( (?P<h>this (week|month|year)))?|what am i looking forward to( (?P<h2>this (week|month|year)))?"),
    ("balance", r"is my life all (obligations|work)|am i doing anything (just )?for (myself|me)|how much of my life is obligations?|am i only doing things i have to"),
    ("curious", r"what am i curious about|what are my curiosity threads"),
    ("neglected", r"what haven'?t i done that (i said )?matter(s|ed)( to me)?|what have i neglected"),
    ("patterns", r"what patterns (do you see|have you (noticed|spotted|found))|(do you )?(see|notice|spot) any patterns|any patterns|(show me |what are )?my patterns|what do i keep (coming back to|saying|doing)"),
    ("life", r"what('?s| is) (actually )?going on in my life( right now)?|give me an overview|show (me )?(my )?(overview|dashboard)"),
]
FILLER = re.compile(r"^(jarvis[, ]+|so[, ]+|ok[, ]+|okay[, ]+|hey[, ]+)+", re.I)


def classify(question):
    q = FILLER.sub("", (question or "").strip().lower())
    q = re.sub(r"[?.!]+$", "", q).strip()
    q = re.sub(r"[\s,]+please$", "", q)
    for intent, pat in INTENTS:
        m = re.fullmatch(pat, q)
        if m:
            return intent, m.groupdict()
    return None, {}


def answer(question, notes_dir=None, today=None):
    """An answer from your stored notes, or None to hand over to the brain."""
    intent, args = classify(question)
    if not intent:
        return None
    g = gather(notes_dir, today)
    if not g["notes"]:
        return _ans(intent, "There's nothing in your notes to go on yet, sir. Capture a few things and ask me again.")
    if intent == "today":
        top = top_today(g)
        if not top:
            return _ans(intent, "Nothing pressing today, sir. No open loops are due.")
        return _ans(intent, f"The {'three things' if len(top) == 3 else 'things'} that matter most today: {_list(top)}.", top)
    if intent == "putting_off":
        p = putting_off(g)
        if not p:
            return _ans(intent, "Nothing's been sitting untouched for more than a week, sir.")
        return _ans(intent, f"These have been open for over a week without moving: {_list(p)}. "
                            "No judgement, just so you can decide what to do with them.", p)
    if intent == "projects":
        ps = [p for p in g["projects"] if p["status"] in ("active", "paused")]
        if not ps:
            return _ans(intent, "No projects yet, sir. When a few loops share a person or place, I'll suggest one.")
        return _ans(intent, f"{len(ps)} project{'s' if len(ps) != 1 else ''}: {_list(ps, lambda p: p['title'] + ('' if p['status'] == 'active' else ' (paused)'))}.", ps)
    if intent == "decided":
        spoken, rid = loops.why(args.get("topic", ""), notes_dir)
        return _ans(intent, spoken, [n for n in g["notes"] if n["id"] == rid])
    if intent == "worrying":
        w = worrying(g)
        if not w:
            return _ans(intent, "Nothing you've written recently reads as a worry or an open problem, sir.")
        return _ans(intent, f"From what you've written: {_list(w)}. Those are your words, not my conclusions.", w)
    if intent == "wanted":
        w = wanted(g)
        if not w:
            return _ans(intent, "You haven't told me about things you'd like to do yet, sir. I'd enjoy hearing them.")
        return _ans(intent, f"Things you've said you'd like: {_list(w)}.", w)
    if intent == "repeated":
        r = repeated(g)
        if not r:
            return _ans(intent, "Nothing's come up three or more times in the last three weeks, sir.")
        return _ans(intent, "In the last three weeks you've mentioned " +
                    ", ".join(f"{name} {n} times" for name, n in r[:4]) + ".")
    if intent == "coming_up":
        u = upcoming(g)
        if not u:
            return _ans(intent, "Nothing dated in the next two weeks, sir.")
        return _ans(intent, f"Coming up: {_list(u, lambda i: i['title'] + ', ' + _fmt(i['when']))}.", u)
    if intent == "before_problem":
        b = before_problem(g)
        if not b:
            return _ans(intent, "Nothing looks likely to turn into a problem soon, sir.")
        return _ans(intent, f"Worth dealing with before it becomes a problem: {_list(b)}.", b)
    if intent == "work":
        w = work(g)
        if not w:
            return _ans(intent, "Nothing open at work that I know of, sir. (I only know what you've noted as work.)")
        top = top_today(dict(g, open=w))
        waiting = [i for i in w if i["loop"]["kind"] == "waiting"]
        line = f"At work: {len(w)} open. Top of the list: {_list(top)}."
        if waiting:
            line += f" Waiting on: {_list(waiting)}."
        return _ans(intent, line, top + waiting)
    if intent == "changed":
        r = recent(g, WEEK)
        done = [n for n in g["notes"] if (n["loop"] or {}).get("state") == "done"
                and _within((n["loop"] or {}).get("closed_at"), g["today"], WEEK)]
        return _ans(intent, f"This past week: {len(r)} new note{'s' if len(r) != 1 else ''}"
                            + (f", {len(done)} thing{'s' if len(done) != 1 else ''} done ({_list(done)})" if done else "")
                            + (f". Newest: {_list(r, n=3)}." if r else "."), r[:3] + done)
    if intent == "learned":
        k = recent(g, RECENT_DAYS, home="KNOWLEDGE")
        if not k:
            return _ans(intent, "Nothing new filed under Knowledge in the last fortnight, sir.")
        return _ans(intent, f"Filed under Knowledge lately: {_list(k)}.", k)
    if intent == "interested":
        i = [n for n in g["notes"] if _age(n) <= 30 and (n["home"] == "WONDER" or n["v"].get("memory") == "interest")]
        if not i:
            return _ans(intent, "Nothing's marked as an interest in the last month, sir.")
        temp = [n for n in i if n["v"].get("temporary")]
        line = f"Lately: {_list(i)}."
        if temp:
            line += " Some of that you described as a passing thing, so I'm not treating it as permanent."
        return _ans(intent, line, i)
    if intent in ("excited", "forward"):
        h = wonder.horizons(notes_dir, g["today"])
        which = args.get("h") or args.get("h2")
        items = sum((h[k] for k in ([which] if which else ["this week", "this month", "this year"])), []) or h["whenever"][:1]
        return _ans(intent, wonder.spoken_horizon(h, which), items)
    if intent == "balance":
        b = wonder.balance(notes_dir, g["today"])
        return _ans(intent, b["line"] or "I can't place enough of your notes as obligations or choices yet to say, sir.")
    if intent == "patterns":      # 3.5.0 (spec §17): observations with their evidence, or "not enough yet"
        import patterns
        r = patterns.detect(notes_dir, g["today"])
        ev = [e for p in r["patterns"][:3] for e in p["evidence"] if e.get("id")]
        return _ans(intent, r["spoken"], [{"title": e["title"], "id": e["id"]} for e in ev])
    if intent == "curious":
        th = wonder.threads(notes_dir, g["today"])
        if th:
            return _ans(intent, "Your curiosity threads: " + ", ".join(
                f"{x['name']} ({len(x['items'])} notes)" for x in th[:4]) + ". No action needed.", th[0]["items"])
        line = wonder.wonder_line(notes_dir, g["today"])
        return _ans(intent, line or "Nothing marked as a curiosity yet, sir. I'd enjoy hearing what interests you.")
    if intent == "neglected":
        n = [i for i in g["open"] if "goal" in i["v"].get("kinds", []) or
             (i["loop"]["kind"] == "to do" and (_days(i["loop"].get("opened_at"), g["today"]) or 0) >= 14)]
        if not n:
            return _ans(intent, "Nothing you've marked as mattering has been left for more than a fortnight, sir.")
        return _ans(intent, f"Still waiting for you: {_list(n)}.", n)
    if intent == "life":
        top, u = top_today(g), upcoming(g, WEEK)
        line = f"{len(g['open'])} open loop{'s' if len(g['open']) != 1 else ''}"
        if top:
            line += f"; most important: {_list(top, n=3)}"
        if u:
            line += f"; this week: {_list(u, lambda i: i['title'], n=2)}"
        return _ans(intent, line[0].upper() + line[1:] + ". The overview's open if you want the rest.", top)
    return None
