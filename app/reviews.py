"""
reviews.py — Personal OS Phase 5 (2.7.0): daily and weekly reviews, and
"is there anything you've forgotten?".

Your choices (2026-09-23): offered in Jarvis AND as a small Windows pop-up
while Jarvis is running; morning 08:00, evening 19:00, weekly Sunday 19:00
(all editable in review_settings.json); the reflective questions are asked
and your answers are saved word for word as a Journal note.

Built only from what Jarvis already knows (records, loops, the inbox) --
nothing is invented, every item points back at a note. Wording is plain and
never a telling-off (spec §9, §27). Not over-notifying (spec §23):
  - each review is offered once per day/week; "later" snoozes 30 minutes
  - skipped three times in a row -> no more pop-ups for that review (said
    once, plainly); you can still ask for it any time
  - an item the forgotten-check has raised three times with nothing done
    moves to a quiet "still here" line instead of being repeated

Standard library only. The pop-up uses Windows' own notification system
through PowerShell, with no window; anywhere else it's simply skipped.
"""

import datetime
import json
import os
import re
import subprocess
import sys
import threading
import time

import records

ROOT = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(ROOT, "review_settings.json")
DEFAULT_SETTINGS = {
    "enabled": True, "popups": True, "auto_sort": True,
    "morning": "08:00", "evening": "19:00", "weekly_day": "Sunday", "weekly_time": "19:00",
    "_help": ("Times are 24-hour HH:MM. weekly_day is a day name. popups: false stops the Windows "
              "pop-ups (reviews are still offered inside Jarvis). enabled: false stops offering "
              "reviews altogether; you can still ask for one any time. auto_sort: false stops Jarvis sorting "
              "one batch of your inbox by itself once a day (it uses 1 of your free daily requests)."),
}
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
SNOOZE_MINUTES = 30
SKIPS_BEFORE_QUIET = 3
RAISES_BEFORE_QUIET = 3
STALE_WAITING_DAYS = 7
DORMANT_PROJECT_DAYS = 14
INBOX_SITTING_DAYS = 3
SOON_DAYS = 3

QUESTIONS = {
    "morning": ["What would make today a good day?"],
    "evening": ["What's still on your mind?", "What was good about today?"],
    "weekly": ["Anything from this week you want to remember?"],
    "forgotten": [],
}
TITLES = {"morning": "Morning review", "evening": "Evening review", "weekly": "Weekly review",
          "forgotten": "Anything forgotten?"}


# ---- settings & state --------------------------------------------------------

def load_settings(path=None):
    path = path or SETTINGS_PATH
    if not os.path.exists(path):
        records._atomic_write_json(path, DEFAULT_SETTINGS)
        return dict(DEFAULT_SETTINGS), None
    try:
        with open(path, encoding="utf-8") as f:
            s = json.load(f)
        out = dict(DEFAULT_SETTINGS)
        for k in ("morning", "evening", "weekly_time"):
            v = s.get(k, out[k])
            if not (isinstance(v, str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v)):
                raise ValueError(f"{k} should look like 08:00")
            out[k] = v
        if s.get("weekly_day", out["weekly_day"]) not in DAYS:
            raise ValueError("weekly_day should be a day name like Sunday")
        out["weekly_day"] = s.get("weekly_day", out["weekly_day"])
        for k in ("enabled", "popups", "auto_sort"):
            if not isinstance(s.get(k, out[k]), bool):
                raise ValueError(f"{k} should be true or false")
            out[k] = s.get(k, out[k])
        return out, None
    except (OSError, ValueError, AttributeError) as e:
        # Left exactly as it is; reviews carry on with the defaults and /diag says why.
        return dict(DEFAULT_SETTINGS), f"review_settings.json couldn't be read ({e}); using the defaults"


def _state_path(notes_dir=None):
    return os.path.join(records.store_dir(notes_dir), "review_state.json")


def load_state(notes_dir=None):
    try:
        with open(_state_path(notes_dir), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"offered": {}, "done": {}, "skips": {}, "snooze_until": {}, "popped": {}, "raised": {}}


def save_state(state, notes_dir=None):
    records._atomic_write_json(_state_path(notes_dir), state)


# ---- what's due ----------------------------------------------------------------

def _hm(s):
    h, m = s.split(":")
    return int(h), int(m)


def period_key(kind, now):
    """The day (daily reviews) or ISO week (weekly) a review belongs to."""
    if kind == "weekly":
        y, w, _ = now.isocalendar()
        return f"{y}-W{w:02d}"
    return now.date().isoformat()


def due_review(now, settings, state):
    """Which review should be offered right now, or None. Evening beats
    morning once it's evening; weekly beats evening on its day."""
    if not settings["enabled"]:
        return None
    cands = []
    for kind in ("morning", "evening"):
        h, m = _hm(settings[kind])
        cands.append((kind, now.replace(hour=h, minute=m, second=0, microsecond=0)))
    if DAYS[now.weekday()] == settings["weekly_day"]:
        h, m = _hm(settings["weekly_time"])
        cands.append(("weekly", now.replace(hour=h, minute=m, second=0, microsecond=0)))
    started = [(k, t) for k, t in cands if now >= t]
    if not started:
        return None
    # the most recently started one wins (weekly sits on top of evening)
    order = {"morning": 0, "evening": 1, "weekly": 2}
    kind, _t = max(started, key=lambda kt: (kt[1], order[kt[0]]))
    key = period_key(kind, now)
    if state.get("done", {}).get(kind) == key or state.get("skipped_at", {}).get(kind) == key:
        return None
    snooze = state.get("snooze_until", {}).get(kind)
    if snooze and now.isoformat() < snooze:
        return None
    return kind


def mark(kind, action, now=None, notes_dir=None):
    """started / done / later / skipped. Returns a line to say, or None."""
    now = now or datetime.datetime.now()
    state = load_state(notes_dir)
    key = period_key(kind, now)
    note = None
    if action in ("started", "done"):
        state.setdefault("done", {})[kind] = key
        state.setdefault("skips", {})[kind] = 0
    elif action == "later":
        state.setdefault("snooze_until", {})[kind] = (now + datetime.timedelta(minutes=SNOOZE_MINUTES)).isoformat()
    elif action == "skipped":
        state.setdefault("skipped_at", {})[kind] = key
        n = state.setdefault("skips", {}).get(kind, 0) + 1
        state["skips"][kind] = n
        if n == SKIPS_BEFORE_QUIET:
            note = (f"You've skipped the {kind} review {n} times in a row, sir, so I'll stop popping it up. "
                    f"It's still here whenever you say \"{kind} review\".")
    else:
        raise ValueError("unknown action")
    save_state(state, notes_dir)
    return note


def popups_quiet(kind, state):
    return state.get("skips", {}).get(kind, 0) >= SKIPS_BEFORE_QUIET


# ---- building a review -----------------------------------------------------------

def _reading(sc):
    for it in reversed(sc.get("interpretations", [])):
        if it.get("kind") == "sorting":
            return it["value"]
    return {}


def _title(sc, notes_dir):
    try:
        return records.title_of(sc["path"], records._read_text(records.abs_path(sc["path"], notes_dir)))
    except OSError:
        return sc["path"]


def _days_since(iso, today):
    try:
        return (today - datetime.date.fromisoformat((iso or "")[:10])).days
    except ValueError:
        return None


def _fmt(iso):
    d = datetime.date.fromisoformat(iso)
    return f"{d.strftime('%A')} {d.day} {d.strftime('%B')}"


def gather(notes_dir=None, today=None):
    today = today or datetime.date.today()
    recs, _ = records.load_all(notes_dir)
    live = [sc for sc in recs.values() if not sc.get("missing")]
    t = today.isoformat()
    out = {"open": [], "closed_today": [], "captured_today": 0, "events": [], "inbox": [],
           "needs_you": [], "projects": [], "journal_buried": [], "followups": [], "mentions": {}}
    by_id = {sc["id"]: sc for sc in live}
    for sc in live:
        v = _reading(sc)
        loop = sc.get("loop")
        item = {"id": sc["id"], "title": _title(sc, notes_dir), "created": sc.get("created"), "v": v, "loop": loop}
        if loop and loop.get("state") == "open":
            out["open"].append(item)
        if loop and loop.get("state") == "done" and (loop.get("closed_at") or "")[:10] == t:
            out["closed_today"].append(item)
        if (sc.get("created") or "")[:10] == t and sc.get("source") not in ("file", "review"):
            out["captured_today"] += 1
        for d in v.get("dates", []):
            if d.get("kind") == "event" and d.get("iso") and d["iso"] >= t:
                out["events"].append(dict(item, when=d["iso"]))
        if sc.get("status") == "unprocessed" and sc.get("home") == "INBOX":
            out["inbox"].append(item)
        if sc.get("status") == "needs_clarification":
            out["needs_you"].append(item)
        if sc.get("project_meta") and sc["project_meta"].get("kind") != "category":
            last = max([h.get("at", "") for h in sc.get("history", [])] +
                       [h.get("at", "") for s in live if (s.get("loop") or {}).get("project") == sc["id"]
                        for h in s.get("history", [])])
            out["projects"].append(dict(item, last=last, status=sc["project_meta"].get("status")))
        if sc.get("home") == "JOURNAL" and v.get("held_back_action_kinds") and v.get("intention") == "maybe":
            out["journal_buried"].append(item)
        if sc.get("followup") and not (loop and loop.get("state") in ("done", "dropped")):
            out["followups"].append(item)
        age = _days_since(sc.get("created"), today)
        if age is not None and age <= 21:
            for name in v.get("people", []) + v.get("places", []) + v.get("organisations", []):
                out["mentions"].setdefault(name, []).append(item)
    return out


def _line(text, record_id=None, tag=None):
    return {"text": text, "record_id": record_id, "tag": tag}


def build(kind, notes_dir=None, today=None, state=None):
    """{"title", "sections": [{"title", "items": [...]}], "spoken", "questions"}."""
    today = today or datetime.date.today()
    t, tomorrow = today.isoformat(), (today + datetime.timedelta(days=1)).isoformat()
    soon = (today + datetime.timedelta(days=SOON_DAYS)).isoformat()
    g = gather(notes_dir, today)
    open_ = g["open"]
    def due(i): return (i["loop"] or {}).get("due")
    overdue = [i for i in open_ if due(i) and due(i) < t]
    due_today = [i for i in open_ if due(i) == t]
    due_tomorrow = [i for i in open_ if due(i) == tomorrow]
    due_soon = [i for i in open_ if due(i) and t < due(i) <= soon]
    waiting = [i for i in open_ if i["loop"]["kind"] == "waiting"]
    to_decide = [i for i in open_ if i["loop"]["kind"] == "to decide"]
    to_solve = [i for i in open_ if i["loop"]["kind"] == "to solve"]
    promises = [i for i in open_ if "commitment" in i["v"].get("kinds", []) or
                (i["loop"]["kind"] == "to do" and i["v"].get("people"))]
    now_items = [i for i in open_ if i not in overdue + due_today and i["loop"]["kind"] == "to do"
                 and not due(i)][:5]
    sections = []

    def add(title, items, fmt):
        if items:
            sections.append({"title": title, "items": [_line(fmt(i), i["id"]) for i in items]})

    if kind == "morning":
        add("What matters today", overdue + due_today + now_items,
            lambda i: i["title"] + (" (was due " + _fmt(due(i)) + ")" if due(i) and due(i) < t else
                                    " (due today)" if due(i) == t else ""))
        add("What's expected today", [e for e in g["events"] if e["when"] == t], lambda i: i["title"])
        add("What could become a problem", [i for i in due_soon] + to_solve,
            lambda i: i["title"] + (f" (due {_fmt(due(i))})" if due(i) else ""))
        add("What you're waiting for", waiting, lambda i: i["title"])
        spoken = _spoken_morning(overdue, due_today, waiting, due_soon)
    elif kind == "evening":
        happened = []
        if g["captured_today"]:
            happened.append(_line(f"You captured {g['captured_today']} thing{'s' if g['captured_today'] != 1 else ''} today."))
        happened += [_line("Done: " + i["title"], i["id"]) for i in g["closed_today"]]
        if happened:
            sections.append({"title": "What happened today", "items": happened})
        add("What you promised someone", promises, lambda i: i["title"])
        add("What needs to happen tomorrow", due_tomorrow + [e for e in g["events"] if e["when"] == tomorrow],
            lambda i: i["title"])
        unresolved = [_line(f"{len(open_)} open loop{'s' if len(open_) != 1 else ''}")] if open_ else []
        if g["inbox"]:
            unresolved.append(_line(f"{len(g['inbox'])} in the inbox, not yet sorted"))
        if g["needs_you"]:
            unresolved.append(_line(f"{len(g['needs_you'])} question{'s' if len(g['needs_you']) != 1 else ''} waiting for you"))
        if unresolved:
            sections.append({"title": "What remains unresolved", "items": unresolved})
        spoken = _spoken_evening(g, due_tomorrow, open_)
        # 2.9.0 (spec §18): at most once a week, one line of wonder. Not a task.
        import wonder
        due_w, _key = wonder.weekly_wonder_due(state or {}, today)
        line = wonder.wonder_line(notes_dir, today) if due_w else None
        if line:
            sections.append({"title": "Something for you", "items": [_line(line)], "wonder": True})
    elif kind in ("weekly", "forgotten"):
        sections = forgotten_sections(g, today, state or {})
        if kind == "weekly":
            weekly = []
            add_w = lambda title, items, fmt: items and weekly.append(
                {"title": title, "items": [_line(fmt(i), i["id"]) for i in items]})
            add_w("Overdue", overdue, lambda i: f"{i['title']} (was due {_fmt(due(i))})")
            add_w("Coming up this week",
                  [i for i in open_ if due(i) and t <= due(i) <= (today + datetime.timedelta(days=7)).isoformat()]
                  + [e for e in g["events"] if e["when"] <= (today + datetime.timedelta(days=7)).isoformat()],
                  lambda i: f"{i['title']} ({_fmt(i.get('when') or due(i))})")
            add_w("Decisions still to make", to_decide, lambda i: i["title"])
            sitting = [i for i in g["inbox"] if (_days_since(i["created"], today) or 0) >= INBOX_SITTING_DAYS]
            add_w("Sitting in the inbox", sitting,
                  lambda i: f"{i['title']} ({_days_since(i['created'], today)} days)")
            add_w("Needs your word", g["needs_you"], lambda i: i["title"])
            import wonder
            bal = wonder.balance(notes_dir, today)
            if bal["line"]:
                weekly.append({"title": "Balance", "items": [_line(bal["line"])]})
            import patterns         # 3.5.0 (spec §17): weekly review -> pattern detection
            pats = patterns.detect(notes_dir, today)
            if pats["patterns"]:
                weekly.append({"title": "Patterns (what the records show)", "items": [
                    _line(p["line"], next((e["id"] for e in p["evidence"] if e.get("id")), None))
                    for p in pats["patterns"][:3]]})
            import maintenance      # 3.0.0: tidy-up suggestions, in one line
            tidy = maintenance.report(notes_dir, today)
            if tidy["duplicates"] or tidy["archive"] or tidy["second_look"]:
                weekly.append({"title": "Tidy up", "items": [_line(tidy["spoken"])]})
            sections = weekly + sections
        spoken = _spoken_forgotten(sections, kind)
    else:
        raise ValueError("unknown review")
    questions = list(QUESTIONS[kind])
    if kind == "weekly":
        import wonder
        if wonder.agency_week(today):              # every other week (spec §20)
            questions.append(wonder.AGENCY_QUESTION)
    return {"kind": kind, "title": TITLES[kind], "sections": sections, "spoken": spoken,
            "questions": questions}


def forgotten_sections(g, today, state):
    """The 'anything forgotten?' search (spec §10)."""
    raised = state.get("raised", {})
    out, quiet = [], []

    def add(title, items, fmt):
        keep = []
        for i in items:
            (quiet if raised.get(i["id"], 0) >= RAISES_BEFORE_QUIET else keep).append(i)
        if keep:
            out.append({"title": title, "items": [_line(fmt(i), i["id"], "raise") for i in keep]})

    add("You asked me to remind you", g["followups"], lambda i: i["title"])
    promises = [i for i in g["open"] if "commitment" in i["v"].get("kinds", [])]
    add("Promises still open", promises, lambda i: i["title"])
    stale = [i for i in g["open"] if i["loop"]["kind"] == "waiting"
             and (_days_since(i["loop"].get("opened_at"), today) or 0) >= STALE_WAITING_DAYS]
    add("Waiting, with no follow-up for a while", stale,
        lambda i: f"{i['title']} ({_days_since(i['loop']['opened_at'], today)} days)")
    never_started = [i for i in g["open"] if i["loop"]["kind"] == "to do"
                     and (_days_since(i["loop"].get("opened_at"), today) or 0) >= 14]
    add("Said you'd do, not started yet", never_started,
        lambda i: f"{i['title']} (since {_days_since(i['loop']['opened_at'], today)} days ago)")
    add("Possibly buried in a journal entry", g["journal_buried"],
        lambda i: f"{i['title']}. Something to do, or just a thought?")
    dormant = [p for p in g["projects"] if p["status"] == "active"
               and (_days_since(p["last"], today) or 0) >= DORMANT_PROJECT_DAYS]
    add("Projects that have gone quiet", dormant,
        lambda p: f"{p['title']}: no activity for {_days_since(p['last'], today)} days")
    soon = (today + datetime.timedelta(days=SOON_DAYS)).isoformat()
    approaching = [i for i in g["open"] if (i["loop"].get("due") or "9") <= soon and i["loop"]["due"] >= today.isoformat()]
    add("Deadlines in the next few days", approaching, lambda i: f"{i['title']} ({_fmt(i['loop']['due'])})")
    add("Dates coming up", [e for e in g["events"] if e["when"] <= (today + datetime.timedelta(days=7)).isoformat()],
        lambda i: f"{i['title']} ({_fmt(i['when'])})")
    looped = {i["id"] for i in g["open"]}
    repeated = []
    for name, items in g["mentions"].items():
        ids = {i["id"] for i in items}
        if len(ids) >= 3 and not ids & looped:
            repeated.append({"id": items[0]["id"], "title": name, "n": len(ids)})
    add("Mentioned a lot, nothing done with it yet", repeated,
        lambda r: f"You've mentioned {r['title']} {r['n']} times in the last three weeks. Anything to act on?")
    if quiet:
        out.append({"title": "Still here (raised before)",
                    "items": [_line(f"{len(quiet)} item{'s' if len(quiet) != 1 else ''} I've mentioned a few times. "
                                    "Worth dropping or archiving?")]})
    return out


def note_raised(review, notes_dir=None):
    """Counts how often each item has been raised by the forgotten check."""
    state = load_state(notes_dir)
    raised = state.setdefault("raised", {})
    for s in review["sections"]:
        for it in s["items"]:
            if it.get("tag") == "raise" and it.get("record_id"):
                raised[it["record_id"]] = raised.get(it["record_id"], 0) + 1
    save_state(state, notes_dir)


def _plural(n, word):
    return f"{n} {word}{'s' if n != 1 else ''}"


def _spoken_morning(overdue, due_today, waiting, due_soon):
    parts = []
    if overdue:
        parts.append(f"{_plural(len(overdue), 'thing')} past {'its' if len(overdue) == 1 else 'their'} date")
    if due_today:
        parts.append(f"{len(due_today)} due today")
    if due_soon:
        parts.append(f"{len(due_soon)} coming up in the next few days")
    if waiting:
        parts.append(f"you're waiting on {_plural(len(waiting), 'thing')}")
    if not parts:
        return "Good morning, sir. Nothing pressing today. What would make it a good day?"
    return "Good morning, sir. " + ", ".join(parts).capitalize() + ". What would make today a good day?"


def _spoken_evening(g, due_tomorrow, open_):
    bits = []
    if g["closed_today"]:
        bits.append(f"{_plural(len(g['closed_today']), 'thing')} done today")
    if due_tomorrow:
        bits.append(f"{len(due_tomorrow)} due tomorrow")
    if open_:
        bits.append(f"{_plural(len(open_), 'open loop')} in all")
    head = "Good evening, sir. " + (", ".join(bits).capitalize() + ". " if bits else "")
    return head + "What's still on your mind?"


def _spoken_forgotten(sections, kind):
    n = sum(len(s["items"]) for s in sections)
    if kind == "weekly":
        return ("Your weekly review, sir: " + (_plural(n, "item") + " worth a look. " if n else
                "nothing needs your attention. ") + "Anything from this week you want to remember?")
    if not n:
        return "Nothing I can see has slipped, sir."
    first = sections[0]
    return f"A few things may have slipped, sir. First: {first['title'].lower()}: {first['items'][0]['text']}."


# ---- saving your answers ----------------------------------------------------------

def save_answers(kind, answers, source="typed", notes_dir=None, now=None):
    """Your reflective answers, word for word, as one Journal note. Empty
    answers are skipped; if there's nothing, nothing is written."""
    answers = [(q, (a or "").strip()) for q, a in answers if isinstance(a, str) and a.strip()]
    if not answers:
        return None
    now = now or datetime.datetime.now()
    root = notes_dir or records.NOTES_DIR
    folder = os.path.join(root, "journal")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{now.date().isoformat()}-{kind}-review.md")
    n = 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(folder, f"{now.date().isoformat()}-{kind}-review-{n}.md")
    lines = [f"# {TITLES[kind]}, {now.day} {now.strftime('%B %Y')}", "", f"Added {now.date().isoformat()}."]
    for q, a in answers:
        lines += ["", f"**{q}**", a]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    sc = records.create_for_capture(path, "\n".join(f"{q} {a}" for q, a in answers),
                                    source=source if source in records.SOURCES else "unknown",
                                    notes_dir=root, extra_event={"how": f"{kind} review"})
    sc["home"], sc["status"] = "JOURNAL", "sorted"
    records._event(sc, "filed in Journal (your own review answers)")
    records.save(sc, root)
    return sc


# ---- the Windows pop-up and the scheduler ------------------------------------------

POPUP_TEXT = {"morning": "Your morning review is ready.", "evening": "Your evening review is ready.",
              "weekly": "Your weekly review is ready."}


def windows_popup(title, text):
    """A normal Windows notification, shown through PowerShell with no
    window. Returns (ok, reason). Never raises."""
    if sys.platform != "win32":
        return False, "not Windows"
    safe = lambda s: s.replace("'", "''").replace("<", "").replace(">", "").replace("&", "and")
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] | Out-Null;"
        "$x = New-Object Windows.Data.Xml.Dom.XmlDocument;"
        f"$x.LoadXml('<toast><visual><binding template=\"ToastGeneric\"><text>{safe(title)}</text>"
        f"<text>{safe(text)}</text></binding></visual></toast>');"
        "$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show("
        "[Windows.UI.Notifications.ToastNotification]::new($x))"
    )
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                           capture_output=True, text=True, timeout=20,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return (r.returncode == 0, (r.stderr or "").strip()[:200] or None)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)


STATUS = {"running": False, "last_check": None, "last_popup": None, "popup_error": None, "settings_problem": None}


def check_once(now=None, notes_dir=None, popup=windows_popup):
    """One scheduler tick: works out what's due, and pops it up once."""
    now = now or datetime.datetime.now()
    settings, problem = load_settings()
    STATUS["settings_problem"], STATUS["last_check"] = problem, now.isoformat(timespec="seconds")
    state = load_state(notes_dir)
    kind = due_review(now, settings, state)
    if not kind:
        return None
    key = period_key(kind, now)
    if settings["popups"] and not popups_quiet(kind, state) and state.get("popped", {}).get(kind) != key:
        ok, reason = popup("Jarvis", POPUP_TEXT[kind] + " Open the Jarvis tab when you're ready.")
        state.setdefault("popped", {})[kind] = key          # once, whatever happened
        save_state(state, notes_dir)
        STATUS["last_popup"] = {"kind": kind, "at": now.isoformat(timespec="seconds"), "ok": ok}
        STATUS["popup_error"] = None if ok else reason
    return kind


def start_scheduler(notes_dir=None, interval=60):
    stop = threading.Event()

    def loop():
        STATUS["running"] = True
        while not stop.is_set():
            try:
                check_once(notes_dir=notes_dir)
            except Exception as e:  # noqa: BLE001 -- a bad tick never stops the server
                STATUS["popup_error"] = f"scheduler: {e}"
            stop.wait(interval)
        STATUS["running"] = False

    threading.Thread(target=loop, name="review-scheduler", daemon=True).start()
    return stop
