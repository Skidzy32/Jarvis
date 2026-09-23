"""
usage_tracker.py — how long you spend in each app and site (2.3.0).

Your request (2026-09-22): time per app/site, kept raw for about a week,
turned into a weekly report, the raw data then discarded; weekly reports
added up into monthly ones, monthly into yearly -- and every report kept.

WHAT IS RECORDED
  Only while Jarvis is running. Every few seconds: which app is in front,
  and -- when it's a browser Jarvis can read -- the SITE's name only
  ("YouTube", "bbc.co.uk"), never a page address, page title or window
  title. Time in a focus session is counted separately too.
  Time when you've been away from the keyboard and mouse for 5 minutes is
  not counted at all (Windows only; elsewhere there's no way to tell).
  Trade-off: a long video watched without touching anything counts as
  away too. "away_after_minutes" in usage/settings.json changes the 5, and
  0 switches it off.
  Anything matching your never-record list (usage/settings.json) is added
  to an "excluded" total with no name at all.

WHERE
  usage/raw/<date>.json                 one small file per day, totals only
  usage/reports/weekly/<year>-W<nn>.json + .md   (the .md is for reading)
  usage/reports/monthly/<year>-<mm>.json + .md
  usage/reports/yearly/<year>.json + .md
  usage/settings.json                   on/off switch + never-record list
  All on this computer only, and git-ignored (like the focus ledger).

WHEN THINGS HAPPEN
  - A week's report is written once the week (Monday-Sunday) is over.
  - A raw day is deleted only when it's more than 7 days old AND its
    week's report exists AND that report, read back from disk, holds the
    same total for that day. If any of that isn't true, it's kept.
  - A week that crosses a month end counts toward the month holding its
    Thursday (the usual week-numbering rule: the month with most of its
    days), so no week is split or counted twice. Monthly reports are
    made from weekly reports, yearly from monthly; since weekly reports
    are never deleted, the bigger ones can always be rebuilt.

This deliberately differs from Prompt 12's "a distraction's name is
stored nowhere": focus sessions still store no names (their ledger is
unchanged); this is a separate record, with its own switch, that you
asked for.

Standard library only.
"""

import datetime
import json
import os
import secrets
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
USAGE_DIR = os.path.join(ROOT, "usage")

SCHEMA = 1
SAMPLE_SECONDS = 5          # how often the front app is read
FLUSH_SECONDS = 60          # how often the day's totals are written to disk
ROLLUP_SECONDS = 3600       # how often reports / clean-up are checked
IDLE_AFTER_MINUTES = 5      # default: no keyboard/mouse this long = away (settings can change it)
RAW_KEEP_DAYS = 7
MAX_TICK_SECONDS = 3 * SAMPLE_SECONDS  # a longer gap (sleep/hibernate) counts one tick only

EXCLUDED_KEY = "(excluded)"

DEFAULT_NEVER_RECORD = [
    # Banking and money
    "bank", "paypal", "monzo", "starling", "revolut", "barclays", "hsbc", "lloyds",
    "natwest", "santander", "halifax", "nationwide", "tsb", "firstdirect", "chase",
    "wise.com", "klarna", "coinbase",
    # Health
    "nhs", "health", "patient", "doctor", "pharmacy",
    # Passwords
    "1password", "bitwarden", "lastpass", "keepass", "dashlane",
]

SETTINGS_HELP = (
    "enabled: true/false switches usage tracking on or off (takes effect within a few "
    "seconds, no restart). never_record: any app or site whose name contains one of these "
    "(upper/lower case doesn't matter) is counted only as 'excluded' time, with no name. "
    "Edit freely; keep the quotes and commas. away_after_minutes: after this long with no "
    "keyboard or mouse input the time isn't counted (you're probably away) -- but that also "
    "skips a long video you watch without touching anything; set 0 to always count."
)


# ---- paths & small helpers --------------------------------------------------

def _paths(base):
    return {
        "raw": os.path.join(base, "raw"),
        "weekly": os.path.join(base, "reports", "weekly"),
        "monthly": os.path.join(base, "reports", "monthly"),
        "yearly": os.path.join(base, "reports", "yearly"),
        "settings": os.path.join(base, "settings.json"),
    }


def _atomic_write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp-{secrets.token_hex(3)}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _write_json(path, data):
    _atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False))


def _read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def iso_week(d):
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_bounds(week):
    """'2026-W39' -> (monday, sunday) dates."""
    y, w = week.split("-W")
    monday = datetime.date.fromisocalendar(int(y), int(w), 1)
    return monday, monday + datetime.timedelta(days=6)


def month_of_week(week):
    """The month holding the week's Thursday."""
    monday, _ = week_bounds(week)
    return (monday + datetime.timedelta(days=3)).strftime("%Y-%m")


def weeks_of_month(month):
    """Every ISO week whose Thursday falls in this month."""
    y, m = map(int, month.split("-"))
    d = datetime.date(y, m, 1)
    out = []
    while d.month == m:
        if d.weekday() == 3:  # Thursday
            out.append(iso_week(d))
        d += datetime.timedelta(days=1)
    return out


def fmt_duration(seconds):
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m"
    return f"{seconds}s"


# ---- settings -----------------------------------------------------------------

def load_settings(base=USAGE_DIR):
    """Returns (settings, problem). A missing file is created with the
    defaults. A file that can't be read is left exactly as it is and
    tracking PAUSES (the privacy-safe choice) until it's fixed; the problem
    is reported on /diag rather than the file being overwritten."""
    path = _paths(base)["settings"]
    if not os.path.exists(path):
        settings = {"enabled": True, "away_after_minutes": IDLE_AFTER_MINUTES,
                    "never_record": list(DEFAULT_NEVER_RECORD), "_help": SETTINGS_HELP}
        _write_json(path, settings)
        return settings, None
    try:
        s = _read_json(path)
        never = s.get("never_record", [])
        if not isinstance(never, list) or not all(isinstance(x, str) for x in never):
            raise ValueError("never_record must be a list of names in quotes")
        away = s.get("away_after_minutes", IDLE_AFTER_MINUTES)
        if isinstance(away, bool) or not isinstance(away, (int, float)) or away < 0:
            raise ValueError("away_after_minutes must be a number, 0 or more")
        enabled = s.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError('enabled must be true or false (no quotes)')
        return {"enabled": enabled, "never_record": never,
                "away_after_minutes": away}, None
    except (OSError, ValueError, AttributeError, TypeError) as e:
        return {"enabled": False, "never_record": [], "away_after_minutes": IDLE_AFTER_MINUTES}, f"usage/settings.json couldn't be read ({e}); tracking is paused until it's fixed"


def is_never_record(label, never_record):
    low = (label or "").lower()
    return any(w.strip() and w.strip().lower() in low for w in never_record)


# ---- turning a read of the screen into a name ---------------------------------

def classify(surface):
    """(kind, label) for a focus_session Surface, or None to count nothing.
    Uses only the surface's short label (site or app name); the Surface
    never carries an address or window title in the first place."""
    kind = getattr(surface, "kind", "none")
    if kind in ("jarvis", "own"):
        return "jarvis", "Jarvis"
    if kind == "tab":
        return "site", surface.label or "(unnamed site)"
    if kind == "app":
        return "app", surface.label or "(unnamed app)"
    return None


# ---- the recorder --------------------------------------------------------------

class UsageTracker:
    def __init__(self, read_surface_fn, in_session_fn, idle_fn=lambda: None,
                 base=USAGE_DIR, clock=time.time, today_fn=None):
        self.read_surface = read_surface_fn
        self.in_session = in_session_fn
        self.idle = idle_fn
        self.base = base
        self.clock = clock
        self.today = today_fn or (lambda: datetime.date.fromtimestamp(self.clock()))
        self.pending = {}          # date -> day-shaped totals not yet on disk
        self.last_tick = None
        self.lock = threading.Lock()
        self.status = {"running": False, "last_sample": None, "last_flush": None,
                       "last_rollup": None, "last_error": None, "paused_reason": None,
                       "away": False}

    # One read of the screen -> seconds added to today's totals in memory.
    def sample(self):
        now = self.clock()
        elapsed = SAMPLE_SECONDS if self.last_tick is None else now - self.last_tick
        self.last_tick = now
        if elapsed <= 0:
            return None
        if elapsed > MAX_TICK_SECONDS:
            elapsed = SAMPLE_SECONDS
        settings, problem = load_settings(self.base)
        self.status["paused_reason"] = problem or (None if settings["enabled"] else "switched off in usage/settings.json")
        if not settings["enabled"]:
            return None
        idle = self.idle()
        away_after = settings["away_after_minutes"] * 60
        if away_after and idle is not None and idle >= away_after:
            self.status["away"] = True
            return None
        self.status["away"] = False
        try:
            surface = self.read_surface()
        except Exception as e:  # noqa: BLE001 -- a bad read skips a tick, never crashes
            self.status["last_error"] = {"at": now_iso(), "error": f"read: {e}"}
            return None
        got = classify(surface)
        if got is None:
            return None
        kind, label = got
        focus = bool(self.in_session())
        day = self.today().isoformat()
        with self.lock:
            d = self.pending.setdefault(day, _empty_day(day))
            if is_never_record(label, settings["never_record"]):
                d["excluded_seconds"] += elapsed
                if focus:
                    d["excluded_focus_seconds"] += elapsed
                key = EXCLUDED_KEY
            else:
                key = f"{kind}:{label}"
                e = d["entries"].setdefault(key, {"kind": kind, "label": label, "seconds": 0, "focus_seconds": 0})
                e["seconds"] += elapsed
                if focus:
                    e["focus_seconds"] += elapsed
            d["tracked_seconds"] += elapsed
            if focus:
                d["focus_seconds"] += elapsed
        self.status["last_sample"] = now_iso()
        return key

    def flush(self):
        """Adds what's in memory to each day's file on disk."""
        with self.lock:
            pending, self.pending = self.pending, {}
        for day, add in pending.items():
            path = os.path.join(_paths(self.base)["raw"], f"{day}.json")
            try:
                current = _read_json(path) if os.path.exists(path) else _empty_day(day)
                _merge_day(current, add)
                _write_json(path, current)
            except Exception as e:  # noqa: BLE001
                # Keep it in memory for the next try rather than losing it.
                with self.lock:
                    _merge_day(self.pending.setdefault(day, _empty_day(day)), add)
                self.status["last_error"] = {"at": now_iso(), "error": f"flush {day}: {e}"}
                return False
        self.status["last_flush"] = now_iso()
        return True

    def rollup(self):
        # Flush first, so the last minutes of a finished week are on disk
        # before its report is written.
        self.flush()
        try:
            result = rollup(self.base, self.today())
            self.status["last_rollup"] = now_iso()
            return result
        except Exception as e:  # noqa: BLE001
            self.status["last_error"] = {"at": now_iso(), "error": f"rollup: {e}"}
            return None

    def run(self, stop_event):
        self.status["running"] = True
        self.rollup()
        last_flush = last_rollup = self.clock()
        try:
            while not stop_event.wait(SAMPLE_SECONDS):
                self.sample()
                if self.clock() - last_flush >= FLUSH_SECONDS:
                    self.flush()
                    last_flush = self.clock()
                if self.clock() - last_rollup >= ROLLUP_SECONDS:
                    self.rollup()
                    last_rollup = self.clock()
        finally:
            self.flush()
            self.status["running"] = False


def _empty_day(day):
    return {"schema": SCHEMA, "date": day, "tracked_seconds": 0, "focus_seconds": 0,
            "excluded_seconds": 0, "excluded_focus_seconds": 0, "entries": {}}


def _merge_day(into, add):
    for k in ("tracked_seconds", "focus_seconds", "excluded_seconds", "excluded_focus_seconds"):
        into[k] = into.get(k, 0) + add.get(k, 0)
    for key, e in add.get("entries", {}).items():
        t = into.setdefault("entries", {}).setdefault(
            key, {"kind": e["kind"], "label": e["label"], "seconds": 0, "focus_seconds": 0})
        t["seconds"] += e["seconds"]
        t["focus_seconds"] += e["focus_seconds"]


# ---- reports -------------------------------------------------------------------

def _combine(parts, **head):
    """Adds up day files or reports into one report body."""
    report = dict(head)
    report.update({"schema": SCHEMA, "generated_at": now_iso(), "tracked_seconds": 0,
                   "focus_seconds": 0, "excluded_seconds": 0, "excluded_focus_seconds": 0,
                   "entries": {}})
    for p in parts:
        _merge_day(report, p)
    return report


def _render_md(report, title):
    lines = [f"# {title}", ""]
    lines.append(f"Tracked while Jarvis was running: **{fmt_duration(report['tracked_seconds'])}** "
                 f"(in focus sessions: {fmt_duration(report['focus_seconds'])}).")
    if report["excluded_seconds"]:
        lines.append(f"On your never-record list (no names kept): {fmt_duration(report['excluded_seconds'])}.")
    if report.get("per_day"):
        lines += ["", "| Day | Tracked |", "|---|---|"]
        for day, secs in sorted(report["per_day"].items()):
            lines.append(f"| {datetime.date.fromisoformat(day).strftime('%a %d %b')} | {fmt_duration(secs)} |")
    rows = sorted(report["entries"].values(), key=lambda e: -e["seconds"])
    if rows:
        lines += ["", "| App / site | Time | In focus sessions |", "|---|---|---|"]
        for e in rows[:40]:
            lines.append(f"| {e['label']} ({e['kind']}) | {fmt_duration(e['seconds'])} | {fmt_duration(e['focus_seconds'])} |")
        if len(rows) > 40:
            rest = sum(e["seconds"] for e in rows[40:])
            lines.append(f"| {len(rows) - 40} others | {fmt_duration(rest)} | |")
    else:
        lines += ["", "Nothing was tracked in this period."]
    return "\n".join(lines) + "\n"


def _save_report(folder, name, report, title):
    _write_json(os.path.join(folder, f"{name}.json"), report)
    _atomic_write(os.path.join(folder, f"{name}.md"), _render_md(report, title))


def _raw_days(base):
    folder = _paths(base)["raw"]
    if not os.path.isdir(folder):
        return {}
    out = {}
    for fn in sorted(os.listdir(folder)):
        if fn.endswith(".json") and len(fn) == 15:
            try:
                out[fn[:-5]] = _read_json(os.path.join(folder, fn))
            except (OSError, ValueError):
                pass  # unreadable: left alone, never deleted (see diag)
    return out


def rollup(base=USAGE_DIR, today=None):
    """Writes any reports that are due, then deletes raw days that are
    safely inside a report. Returns what it did."""
    today = today or datetime.date.today()
    p = _paths(base)
    done = {"weekly": [], "monthly": [], "yearly": [], "deleted_raw": [], "kept_raw_unverified": []}
    days = _raw_days(base)

    # Weekly: once the week is over, from its raw days. If a report already
    # exists and a raw day of that week isn't in it yet, it's added in.
    by_week = {}
    for day in days:
        by_week.setdefault(iso_week(datetime.date.fromisoformat(day)), []).append(day)
    for week, wdays in sorted(by_week.items()):
        monday, sunday = week_bounds(week)
        if sunday >= today:
            continue
        path = os.path.join(p["weekly"], f"{week}.json")
        existing = _read_json(path) if os.path.exists(path) else None
        have = set(existing.get("per_day", {})) if existing else set()
        missing = [d for d in wdays if d not in have]
        if not missing:
            continue
        parts = ([existing] if existing else []) + [days[d] for d in missing]
        report = _combine(parts, period="week", week=week, month=month_of_week(week),
                          start=monday.isoformat(), end=sunday.isoformat())
        report["per_day"] = dict(existing.get("per_day", {})) if existing else {}
        for d in missing:
            report["per_day"][d] = days[d]["tracked_seconds"]
        report["days"] = sorted(report["per_day"])
        title = f"Usage — week {week.split('-W')[1]}, {monday.strftime('%d %b')} to {sunday.strftime('%d %b %Y')}"
        _save_report(p["weekly"], week, report, title)
        done["weekly"].append(week)

    # Monthly: once every week counted toward it is over; rebuilt if the set
    # of weekly reports behind it has changed.
    weekly = {}
    if os.path.isdir(p["weekly"]):
        for fn in os.listdir(p["weekly"]):
            if fn.endswith(".json"):
                try:
                    r = _read_json(os.path.join(p["weekly"], fn))
                    weekly[r["week"]] = r
                except (OSError, ValueError, KeyError):
                    pass
    months = sorted({r["month"] for r in weekly.values()})
    for month in months:
        weeks = weeks_of_month(month)
        if week_bounds(weeks[-1])[1] >= today:
            continue
        have = [weekly[w] for w in weeks if w in weekly]
        path = os.path.join(p["monthly"], f"{month}.json")
        sources = sorted(r["week"] for r in have)
        if os.path.exists(path):
            try:
                if _read_json(path).get("weeks") == sources and \
                        _read_json(path).get("sources_generated") == [weekly[w]["generated_at"] for w in sources]:
                    continue
            except (OSError, ValueError):
                pass
        report = _combine(have, period="month", month=month)
        report["weeks"] = sources
        report["sources_generated"] = [weekly[w]["generated_at"] for w in sources]
        report["per_week"] = {r["week"]: r["tracked_seconds"] for r in have}
        name = datetime.date.fromisoformat(month + "-01").strftime("%B %Y")
        _save_report(p["monthly"], month, report, f"Usage — {name}")
        done["monthly"].append(month)

    # Yearly: once December's report can exist; from the monthly reports.
    monthly = {}
    if os.path.isdir(p["monthly"]):
        for fn in os.listdir(p["monthly"]):
            if fn.endswith(".json"):
                try:
                    r = _read_json(os.path.join(p["monthly"], fn))
                    monthly[r["month"]] = r
                except (OSError, ValueError, KeyError):
                    pass
    for year in sorted({m[:4] for m in monthly}):
        dec_weeks = weeks_of_month(f"{year}-12")
        if week_bounds(dec_weeks[-1])[1] >= today:
            continue
        have = [monthly[m] for m in sorted(monthly) if m.startswith(year)]
        sources = [r["month"] for r in have]
        stamps = [r["generated_at"] for r in have]
        path = os.path.join(p["yearly"], f"{year}.json")
        if os.path.exists(path):
            try:
                old = _read_json(path)
                if old.get("months") == sources and old.get("sources_generated") == stamps:
                    continue
            except (OSError, ValueError):
                pass
        report = _combine(have, period="year", year=year)
        report["months"] = sources
        report["sources_generated"] = stamps
        report["per_month"] = {r["month"]: r["tracked_seconds"] for r in have}
        _save_report(p["yearly"], year, report, f"Usage — {year}")
        done["yearly"].append(year)

    # Clean-up: a raw day goes only when it's old enough AND its week's
    # report, read back from disk now, holds the same total for that day.
    for day, data in _raw_days(base).items():
        d = datetime.date.fromisoformat(day)
        if (today - d).days <= RAW_KEEP_DAYS:
            continue
        path = os.path.join(p["weekly"], f"{iso_week(d)}.json")
        try:
            report = _read_json(path)
            ok = report.get("per_day", {}).get(day) == data["tracked_seconds"]
        except (OSError, ValueError):
            ok = False
        if ok:
            os.remove(os.path.join(p["raw"], f"{day}.json"))
            done["deleted_raw"].append(day)
        else:
            done["kept_raw_unverified"].append(day)
    return done


def week_so_far(base=USAGE_DIR, today=None, pending=None):
    """This week's totals from the raw days (+ whatever's still in memory),
    for /usage/status and the checks. Not saved anywhere."""
    today = today or datetime.date.today()
    week = iso_week(today)
    parts = [d for day, d in _raw_days(base).items() if iso_week(datetime.date.fromisoformat(day)) == week]
    for day, d in (pending or {}).items():
        if iso_week(datetime.date.fromisoformat(day)) == week:
            parts.append(d)
    return _combine(parts, period="week so far", week=week)


def diag(base=USAGE_DIR, tracker=None):
    p = _paths(base)
    if os.path.isdir(base):
        settings, problem = load_settings(base)
    else:
        settings, problem = {"enabled": True, "never_record": DEFAULT_NEVER_RECORD,
                             "away_after_minutes": IDLE_AFTER_MINUTES}, None

    def count(folder):
        return len([f for f in os.listdir(folder) if f.endswith(".json")]) if os.path.isdir(folder) else 0

    unreadable = []
    if os.path.isdir(p["raw"]):
        for fn in os.listdir(p["raw"]):
            if fn.endswith(".json"):
                try:
                    _read_json(os.path.join(p["raw"], fn))
                except (OSError, ValueError):
                    unreadable.append(fn)
    return {
        "enabled": settings["enabled"],
        "settings_problem": problem,
        "never_record_entries": len(settings["never_record"]),
        "away_after_minutes": settings["away_after_minutes"],
        "raw_days": count(p["raw"]),
        "unreadable_raw_days": unreadable,
        "reports": {"weekly": count(p["weekly"]), "monthly": count(p["monthly"]), "yearly": count(p["yearly"])},
        "recorder": dict(tracker.status) if tracker else {"running": False},
    }


# ---- running it inside the server ----------------------------------------------

_tracker = None
_stop = None


def start(read_surface_fn, in_session_fn, idle_fn, base=USAGE_DIR):
    global _tracker, _stop
    if _tracker is not None:
        return _tracker
    _tracker = UsageTracker(read_surface_fn, in_session_fn, idle_fn, base=base)
    _stop = threading.Event()
    threading.Thread(target=_tracker.run, args=(_stop,), name="usage-tracker", daemon=True).start()
    return _tracker


def stop():
    global _tracker, _stop
    if _stop:
        _stop.set()
    t = _tracker
    _tracker = _stop = None
    if t:
        t.flush()


def current():
    return _tracker
