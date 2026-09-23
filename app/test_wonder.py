"""
Tests for Personal OS Phase 8 (2.9.0): wonder, anticipation, agency.
Fixed dates, stand-in model, throwaway folders; your real notes are never touched.
Run: python3 test_wonder.py
"""
import datetime, os, re, shutil, tempfile
import records, reviews, sorting, views, wonder
from testkit import StandIn, reading, make_notes, note_hashes, rec_for

PASS = FAIL = 0


def check(label, ok):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
    print(f"  [{'OK' if ok else 'FAIL'}]{'   ' if ok else ' '}{label}")


today = datetime.date(2026, 9, 23)          # a Wednesday, ISO week 39
N = tempfile.mkdtemp(prefix="jarvis-wonder-")
texts = ["Gig at the Academy on Saturday", "Iceland trip in March", "Learn to make sourdough",
         "Pay the council tax", "Renew car insurance", "Book the MOT", "Do the tax return",
         "Stargazing at Kielder in October", "Read about black holes", "Telescope prices",
         "Always wanted to ride the Trans-Siberian"]
paths = make_notes(N, texts)
before = note_hashes(N)
sorting.sort_inbox(StandIn({
    "Gig": reading(home="WONDER", kinds=["experience"], nature="desire",
                   dates=[{"text": "Saturday", "kind": "event", "iso": "2026-09-26"}]),
    "Iceland": reading(home="WONDER", kinds=["wonder"], nature="desire", places=["Iceland"],
                       dates=[{"text": "March", "kind": "event", "iso": "2027-03-01"}]),
    "sourdough": reading(home="SOMEDAY", kinds=["idea"], nature="curiosity"),
    "council tax": reading(home="NOW", kinds=["task"], intention="act", nature="obligation"),
    "insurance": reading(home="NOW", kinds=["task"], intention="act", nature="obligation"),
    "MOT": reading(home="NOW", kinds=["task"], intention="act", nature="responsibility"),
    "tax return": reading(home="NOW", kinds=["task"], intention="act", nature="obligation"),
    "Kielder": reading(home="WONDER", kinds=["wonder"], nature="curiosity", themes=["astronomy"],
                       dates=[{"text": "October", "kind": "event", "iso": "2026-10-17"}]),
    "black holes": reading(home="WONDER", kinds=["wonder"], nature="curiosity", themes=["astronomy"]),
    "Telescope": reading(home="WONDER", kinds=["wonder"], nature="curiosity", themes=["astronomy"]),
    "Trans-Siberian": reading(home="WONDER", kinds=["wonder"], nature="desire"),
}), {}, notes_dir=N)

print("Test 1: looking forward to (spec §19)")
h = wonder.horizons(N, today)
check("this week: the gig", [i["title"] for i in h["this week"]] == ["Gig at the Academy on Saturday"])
check("this month: Kielder", [i["title"] for i in h["this month"]] == ["Stargazing at Kielder in October"])
check("this year: Iceland", [i["title"] for i in h["this year"]] == ["Iceland trip in March"])
check("undated wishes go to 'whenever', never to a horizon they didn't give",
      {"Learn to make sourdough", "Always wanted to ride the Trans-Siberian"} <= {i["title"] for i in h["whenever"]})
check("obligations are not 'things to look forward to'", not any("tax" in i["title"].lower()
      for k in h for i in h[k]))
check("no interest became a task or a loop", all(rec_for(N, p).get("loop") is None
      for p in paths if any(w in p for w in ("n00", "n01", "n02", "n07", "n08", "n09", "n10"))))
s = wonder.spoken_horizon(h)
check("spoken: week, month, year, and the undated wishes", "this week: 'Gig" in s and "this year: 'Iceland" in s
      and "whenever you fancy it" in s)
check("'what's there to look forward to this month'", views.answer("what's there to look forward to this month", N, today)["spoken"]
      == "To look forward to, this month: 'Stargazing at Kielder in October'.")

print("\nTest 2: balance (spec §20)")
b = wonder.balance(N, today)
check("counts by nature", b["counts"] == {"obligation": 3, "responsibility": 1, "choice": 0, "desire": 3, "curiosity": 4})
check("a fair mix -> said plainly, no nudge", b["line"].startswith("A fair mix: 4 obligations") and wonder.AGENCY_QUESTION not in b["line"])
O = tempfile.mkdtemp(prefix="jarvis-wonder2-")
make_notes(O, [f"chore {i}" for i in range(7)] + ["a walk"])
sorting.sort_inbox(StandIn({"chore": reading(home="NOW", kinds=["task"], intention="act", nature="obligation"),
                            "walk": reading(home="WONDER", nature="desire")}), {}, notes_dir=O)
b2 = wonder.balance(O, today)
check("mostly obligations -> gently asks the agency question", b2["line"].startswith("Of the 8 things I can place, 7 are obligations")
      and b2["line"].endswith(wonder.AGENCY_QUESTION))
check("unclassified notes aren't guessed", wonder.balance(tempfile.mkdtemp(), today)["line"] is None)
check("'am I doing anything for myself'", views.answer("am I doing anything for myself", O, today)["spoken"] == b2["line"])

print("\nTest 3: wonder lines (spec §18) and where they appear")
th = wonder.threads(N, today)
check("curiosity thread: astronomy, 3 notes", th[0]["name"] == "astronomy" and len(th[0]["items"]) == 3)
line = wonder.wonder_line(N, today)
check("the wonder line names the thread and asks nothing of you", line.startswith("You have 3 curiosity threads around astronomy")
      and "No action needed" in line)
check("'what am I curious about'", views.answer("what am I curious about", N, today)["spoken"].startswith("Your curiosity threads: astronomy (3 notes)"))
ev = reviews.build("evening", N, today=today, state={})
check("evening review: one 'Something for you' line", [s["title"] for s in ev["sections"]].count("Something for you") == 1)
ev2 = reviews.build("evening", N, today=today, state={"wonder_week": "2026-W39"})
check("...but only once a week", "Something for you" not in [s["title"] for s in ev2["sections"]])
check("weekly review asks the agency question every other week",
      wonder.AGENCY_QUESTION not in reviews.build("weekly", N, today=today)["questions"]
      and wonder.AGENCY_QUESTION in reviews.build("weekly", N, today=today + datetime.timedelta(days=7))["questions"])
check("weekly review has a Balance line", "Balance" in [s["title"] for s in reviews.build("weekly", N, today=today)["sections"]])
ov = views.overview(N, today)
check("overview: 'Looking forward to' and 'Balance'", {"Looking forward to", "Balance"} <= {s["title"] for s in ov["life"]})
R = tempfile.mkdtemp(prefix="jarvis-wonder3-")
rp = make_notes(R, ["Would love to learn the cello"])
sorting.sort_inbox(StandIn({"cello": reading(home="WONDER", nature="desire")}), {}, notes_dir=R)
check("a wish untouched for months -> 'Still appealing?'",
      "about 6 months ago. Still appealing?" in wonder.wonder_line(R, today + datetime.timedelta(days=185)))
check("nature from the model is checked", sorting.validate({"nature": "mandatory"}, "x")["nature"] is None)
check("NOTES UNCHANGED", note_hashes(N) == before)
for d in (N, O, R):
    shutil.rmtree(d, ignore_errors=True)
print(f"\n{PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)
