"""
Tests for usage_tracker.py (2.3.0). A fake clock and fake screen reads
drive the real recorder, so weeks and months of use can be checked in
seconds. Everything goes to a throwaway folder; your real usage/ folder
is never touched.
Run: python3 test_usage_tracker.py
"""

import datetime
import json
import os
import shutil
import tempfile
import threading
import time

import focus_session
import usage_tracker as ut

PASS = 0
FAIL = 0


def check(label, condition):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK]   {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}")


Surface = focus_session.Surface


class Fake:
    """A clock you move by hand plus whatever is 'in front' right now."""
    def __init__(self, start):
        self.t = start
        self.surface = Surface("none")
        self.focus = False
        self.idle = None

    def tab(self, host):
        # Build it exactly as the real reader does, from a real tab dict.
        self.surface = focus_session._surface_from_tab(
            {"url": f"https://{host}/some/private/page?id=123", "title": "Private page title"},
            "Chrome", "apphash")

    def app(self, display_name, process="thing.exe"):
        self.surface = focus_session._read_surface(
            None, lambda: {"title": "Secret window title", "process": process, "pid": 1,
                           "display_name": display_name})


def tracker(base, fake):
    return ut.UsageTracker(lambda: fake.surface, lambda: fake.focus, lambda: fake.idle,
                           base=base, clock=lambda: fake.t)


def run_for(tr, fake, seconds):
    for _ in range(int(seconds // ut.SAMPLE_SECONDS)):
        fake.t += ut.SAMPLE_SECONDS
        tr.sample()


def ts(y, m, d, h=10):
    return time.mktime((y, m, d, h, 0, 0, 0, 0, -1))


def all_text(base):
    out = ""
    for dirpath, _d, files in os.walk(base):
        for fn in files:
            out += open(os.path.join(dirpath, fn), encoding="utf-8").read()
    return out


# ---------------------------------------------------------------------------
print("Test 1: recording -- names only, focus time marked, nothing private")
B = tempfile.mkdtemp(prefix="jarvis-usage-")
f = Fake(ts(2026, 9, 14))            # a Monday
tr = tracker(B, f)
tr.sample()                          # first read starts the clock
f.tab("www.youtube.com"); run_for(tr, f, 600)
f.tab("docs.python.org"); f.focus = True; run_for(tr, f, 300)
f.focus = False
f.app("Discord", "Discord.exe"); run_for(tr, f, 120)
f.surface = Surface("jarvis"); run_for(tr, f, 60)
check("flush writes the day", tr.flush())
day = json.load(open(os.path.join(B, "raw", "2026-09-14.json")))
e = day["entries"]
check("YouTube 10 min, as a site", e["site:YouTube"]["seconds"] == 600 and e["site:YouTube"]["kind"] == "site")
check("docs.python.org 5 min, all of it in a focus session",
      e["site:docs.python.org"]["seconds"] == 300 and e["site:docs.python.org"]["focus_seconds"] == 300)
check("Discord 2 min, as an app", e["app:Discord"]["seconds"] == 120)
check("Jarvis's own tab counted as 'Jarvis'", e["jarvis:Jarvis"]["seconds"] == 60)
check("day totals add up", day["tracked_seconds"] == 1080 and day["focus_seconds"] == 300)
text = all_text(B)
check("no page address, path, query, page title or window title anywhere on disk",
      not any(s in text for s in ("/some/private", "id=123", "Private page title", "Secret window title", "https://")))

print("\nTest 2: never-record list -> time counted, no name kept")
f.tab("onlinebanking.barclays.co.uk"); run_for(tr, f, 90)
f.app("1Password", "1Password.exe"); run_for(tr, f, 30)
tr.flush()
day = json.load(open(os.path.join(B, "raw", "2026-09-14.json")))
check("both counted as excluded time", day["excluded_seconds"] == 120)
recorded = all_text(os.path.join(B, "raw")).lower()   # (settings.json lists them, by design)
check("neither name is anywhere in the recorded data", "barclays" not in recorded and "1password" not in recorded)
settings = json.load(open(os.path.join(B, "settings.json")))
settings["never_record"].append("discord")
json.dump(settings, open(os.path.join(B, "settings.json"), "w"))
f.app("Discord", "Discord.exe"); run_for(tr, f, 60)
tr.flush()
day = json.load(open(os.path.join(B, "raw", "2026-09-14.json")))
check("your own addition takes effect straight away", day["excluded_seconds"] == 180 and day["entries"]["app:Discord"]["seconds"] == 120)

print("\nTest 3: switched off / away / asleep -> nothing counted")
settings["enabled"] = False
json.dump(settings, open(os.path.join(B, "settings.json"), "w"))
before = day["tracked_seconds"]
f.tab("www.youtube.com"); run_for(tr, f, 300)
tr.flush()
check("switched off: nothing added", json.load(open(os.path.join(B, "raw", "2026-09-14.json")))["tracked_seconds"] == before)
check("...and the reason is reported", "switched off" in tr.status["paused_reason"])
settings["enabled"] = True
json.dump(settings, open(os.path.join(B, "settings.json"), "w"))
f.idle = 400; run_for(tr, f, 300); tr.flush()
check("away 5+ minutes: nothing added", json.load(open(os.path.join(B, "raw", "2026-09-14.json")))["tracked_seconds"] == before)
settings["away_after_minutes"] = 0
json.dump(settings, open(os.path.join(B, "settings.json"), "w"))
run_for(tr, f, 60); tr.flush()
check("away_after_minutes 0 -> counted anyway (the long-video case)",
      json.load(open(os.path.join(B, "raw", "2026-09-14.json")))["tracked_seconds"] == before + 60)
settings["away_after_minutes"] = 5
json.dump(settings, open(os.path.join(B, "settings.json"), "w"))
f.idle = None
before += 60
f.t += 3 * 3600; tr.sample(); tr.flush()      # computer asleep for 3 hours
check("a 3-hour sleep counts as one tick, not 3 hours",
      json.load(open(os.path.join(B, "raw", "2026-09-14.json")))["tracked_seconds"] == before + ut.SAMPLE_SECONDS)

print("\nTest 4: a broken settings file -> paused, and left exactly as it was")
open(os.path.join(B, "settings.json"), "w").write('{"enabled": "yes please",')
before_bytes = open(os.path.join(B, "settings.json")).read()
t0 = json.load(open(os.path.join(B, "raw", "2026-09-14.json")))["tracked_seconds"]
run_for(tr, f, 60); tr.flush()
check("nothing recorded", json.load(open(os.path.join(B, "raw", "2026-09-14.json")))["tracked_seconds"] == t0)
check("file untouched", open(os.path.join(B, "settings.json")).read() == before_bytes)
check("problem shown on diag", "couldn't be read" in (ut.diag(B, tr)["settings_problem"] or ""))
json.dump({"enabled": "false"}, open(os.path.join(B, "settings.json"), "w"))
check('"enabled": "false" (in quotes) is refused, not read as on', ut.load_settings(B)[1] is not None)
shutil.rmtree(B)

print("\nTest 5: a flush that fails keeps the data in memory for next time")
B = tempfile.mkdtemp(prefix="jarvis-usage-")
f = Fake(ts(2026, 9, 14)); tr = tracker(B, f); tr.sample()
f.tab("www.youtube.com"); run_for(tr, f, 60)
real = ut._write_json
calls = {"n": 0}
def flaky(path, data):
    if "raw" in path and calls["n"] == 0:
        calls["n"] += 1
        raise OSError("disk full")
    return real(path, data)
ut._write_json = flaky
check("flush reports failure", tr.flush() is False and "disk full" in tr.status["last_error"]["error"])
ut._write_json = real
run_for(tr, f, 60)
check("next flush writes both minutes", tr.flush() and
      json.load(open(os.path.join(B, "raw", "2026-09-14.json")))["tracked_seconds"] == 120)

print("\nTest 6: midnight -- time goes to the right day")
f.t = ts(2026, 9, 14, 23) + 3540; tr.last_tick = f.t    # 23:59
f.tab("www.reddit.com"); run_for(tr, f, 120)
tr.flush()
a = json.load(open(os.path.join(B, "raw", "2026-09-14.json")))["entries"]["site:Reddit"]["seconds"]
b = json.load(open(os.path.join(B, "raw", "2026-09-15.json")))["entries"]["site:Reddit"]["seconds"]
check("split across the two days", a + b == 120 and a > 0 and b > 0)
shutil.rmtree(B)

# ---------------------------------------------------------------------------
print("\nTest 7: weeks, months, years -- reports, and raw days deleted only when safe")
B = tempfile.mkdtemp(prefix="jarvis-usage-")
f = Fake(ts(2026, 9, 7))
tr = tracker(B, f); tr.sample()
# Use every day from Mon 7 Sep to Sun 4 Oct 2026: 1 hour YouTube + 30 min in a session.
d = datetime.date(2026, 9, 7)
expected_days = []
while d <= datetime.date(2026, 10, 4):
    f.t = ts(d.year, d.month, d.day); tr.last_tick = f.t
    f.tab("www.youtube.com"); f.focus = False; run_for(tr, f, 3600)
    f.tab("github.com"); f.focus = True; run_for(tr, f, 1800)
    tr.flush()
    expected_days.append(d.isoformat())
    d += datetime.timedelta(days=1)
f.focus = False
check("28 raw days on disk", len(os.listdir(os.path.join(B, "raw"))) == 28)

r = ut.rollup(B, today=datetime.date(2026, 9, 16))      # Wed of week 38
check("mid-week: week 37 reported, week 38 not yet", r["weekly"] == ["2026-W37"])
w37 = json.load(open(os.path.join(B, "reports", "weekly", "2026-W37.json")))
check("week 37 = 7 days x 1.5h, 30 min of it focus per day",
      w37["tracked_seconds"] == 7 * 5400 and w37["focus_seconds"] == 7 * 1800 and len(w37["days"]) == 7)
check("week 37's per-app totals", w37["entries"]["site:YouTube"]["seconds"] == 7 * 3600)
check("readable .md written beside it", "YouTube" in open(os.path.join(B, "reports", "weekly", "2026-W37.md")).read())
check("raw days only 2-9 days old: those >7 days old and verified deleted, the rest kept",
      r["deleted_raw"] == ["2026-09-07", "2026-09-08"] and os.path.exists(os.path.join(B, "raw", "2026-09-09.json")))
check("nothing is 'unverified'", r["kept_raw_unverified"] == [])
r2 = ut.rollup(B, today=datetime.date(2026, 9, 16))
check("running it again does nothing new", r2["weekly"] == [] and r2["deleted_raw"] == [])

# Tamper: a report that doesn't match its raw day -> the raw day is kept.
w = json.load(open(os.path.join(B, "reports", "weekly", "2026-W37.json")))
w["per_day"]["2026-09-10"] = 1
json.dump(w, open(os.path.join(B, "reports", "weekly", "2026-W37.json"), "w"))
r = ut.rollup(B, today=datetime.date(2026, 9, 20))
check("a report that doesn't match -> that raw day kept, not deleted",
      "2026-09-10" in r["kept_raw_unverified"] and os.path.exists(os.path.join(B, "raw", "2026-09-10.json")))
w["per_day"]["2026-09-10"] = 5400
json.dump(w, open(os.path.join(B, "reports", "weekly", "2026-W37.json"), "w"))

r = ut.rollup(B, today=datetime.date(2026, 10, 6))       # after Sun 4 Oct
check("all four weeks reported by 6 Oct", sorted(os.listdir(os.path.join(B, "reports", "weekly")))
      == sorted(f"2026-W{n}.{x}" for n in (37, 38, 39, 40) for x in ("json", "md")))
m9 = json.load(open(os.path.join(B, "reports", "monthly", "2026-09.json")))
check("September = the weeks with data whose Thursday is in September (W40's Thursday is 1 Oct)",
      m9["weeks"] == ["2026-W37", "2026-W38", "2026-W39"])
check("September's total = those weeks added up", m9["tracked_seconds"] == 3 * 7 * 5400)
check("no October report yet (its last week isn't over)", not os.path.exists(os.path.join(B, "reports", "monthly", "2026-10.json")))
check("no year report yet", not os.listdir(os.path.join(B, "reports", "yearly")) if os.path.isdir(os.path.join(B, "reports", "yearly")) else True)
check("raw days older than 7 days all deleted, newer kept",
      sorted(os.listdir(os.path.join(B, "raw"))) == [f"2026-{d}.json" for d in
                                                     ("09-29", "09-30", "10-01", "10-02", "10-03", "10-04")])

r = ut.rollup(B, today=datetime.date(2027, 1, 10))
check("by Jan 2027: October month report, and the 2026 year report",
      os.path.exists(os.path.join(B, "reports", "monthly", "2026-10.json"))
      and os.path.exists(os.path.join(B, "reports", "yearly", "2026.json")))
y = json.load(open(os.path.join(B, "reports", "yearly", "2026.json")))
check("the year = every tracked second, counted once", y["tracked_seconds"] == 28 * 5400)
check("the year's focus time too", y["focus_seconds"] == 28 * 1800)
check("every raw day gone, every report kept", os.listdir(os.path.join(B, "raw")) == []
      and len(os.listdir(os.path.join(B, "reports", "weekly"))) == 8)

# The bigger reports can be rebuilt from the weekly ones.
os.remove(os.path.join(B, "reports", "monthly", "2026-09.json"))
os.remove(os.path.join(B, "reports", "yearly", "2026.json"))
ut.rollup(B, today=datetime.date(2027, 1, 10))
check("deleted month/year reports are rebuilt from the weekly ones, same totals",
      json.load(open(os.path.join(B, "reports", "yearly", "2026.json")))["tracked_seconds"] == 28 * 5400)
shutil.rmtree(B)

print("\nTest 8: a week crossing a month end is counted once, in one month")
check("week containing Thu 1 Oct 2026 -> October", ut.month_of_week("2026-W40") == "2026-10")
check("week of Mon 28 Dec 2026 (Thu 31 Dec) -> December", ut.month_of_week("2026-W53") == "2026-12")
all_weeks = [w for m in range(1, 13) for w in ut.weeks_of_month(f"2026-{m:02d}")]
check("2026's weeks: every one in exactly one month", len(all_weeks) == len(set(all_weeks)) == 53)

print("\nTest 9: the recorder thread runs, and stops cleanly with its data saved")
B = tempfile.mkdtemp(prefix="jarvis-usage-")
real_sample = ut.SAMPLE_SECONDS
ut.SAMPLE_SECONDS = 0.05
f = Fake(time.time()); f.tab("www.youtube.com")
tr2 = ut.UsageTracker(lambda: f.surface, lambda: False, lambda: None, base=B)
stop = threading.Event()
th = threading.Thread(target=tr2.run, args=(stop,)); th.start()
time.sleep(0.6); stop.set(); th.join(3)
ut.SAMPLE_SECONDS = real_sample
today = datetime.date.today().isoformat()
check("thread stopped", not th.is_alive() and tr2.status["running"] is False)
check("its time was flushed on the way out",
      json.load(open(os.path.join(B, "raw", f"{today}.json")))["entries"]["site:YouTube"]["seconds"] > 0)
shutil.rmtree(B)

print(f"\n{PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)
