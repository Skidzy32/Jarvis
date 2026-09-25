"""
Tests for Personal OS Phase 7 (3.5.0): pattern detection.
Fixed dates, stand-in model, throwaway folders; your real notes and usage
reports are never touched.  Run: python3 test_patterns.py
"""
import datetime, json, os, re, shutil, tempfile
import loops, patterns, records, reviews, sorting, views
from testkit import StandIn, reading, make_notes, note_hashes, rec_for

PASS = FAIL = 0
def check(label, ok):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
    print(f"  [{'OK' if ok else 'FAIL'}]{'   ' if ok else ' '}{label}")

today = datetime.date(2026, 9, 23)           # a Wednesday
def ago(days): return (today - datetime.timedelta(days=days)).isoformat() + "T09:00:00"
def mondays_ago(k): return (today - datetime.timedelta(days=2 + 7 * k)).isoformat() + "T21:00:00"   # 21 Sep = Monday

EMPTY_USAGE = tempfile.mkdtemp(prefix="jarvis-usage-empty-")

print("Test 1: not enough data -> says so, with the numbers, and offers nothing")
S = tempfile.mkdtemp(prefix="jarvis-pat-small-")
make_notes(S, ["Call Dave", "Dave about the van", "Dave's birthday"])
sorting.sort_inbox(StandIn({"Dave": reading(people=["Dave"])}), {}, notes_dir=S)
r = patterns.detect(S, max(today, datetime.date.today()), usage_dir=EMPTY_USAGE)   # notes made by the real clock
check("not ready, no patterns even though Dave is in 3 notes", not r["ready"] and r["patterns"] == [])
check("spoken says not enough yet, with counts", r["spoken"].startswith("Not enough yet") and "3 sorted notes" in r["spoken"]
      and str(patterns.MIN_NOTES) in r["spoken"])
check("/ask route answers the same", views.answer("what patterns do you see?", S, today)["spoken"].startswith("Not enough yet"))
check("empty notes folder is fine", not patterns.detect(tempfile.mkdtemp(), today, usage_dir=EMPTY_USAGE)["ready"])

print("Test 2: enough history -> observations with evidence")
N = tempfile.mkdtemp(prefix="jarvis-pat-")
spec = [  # (text, reading, created)
    ("Dave said the van is ready", reading(people=["Dave"]), ago(40)),
    ("Lunch with Dave was good", reading(people=["Dave"]), ago(25)),
    ("Dave owes me a tenner", reading(people=["Dave"]), ago(10)),
    ("Ask Dave about Sunday", reading(people=["Dave"]), ago(2)),
    ("Iceland looked amazing on telly", reading(home="WONDER", kinds=["wonder"], places=["Iceland"]), ago(50)),
    ("Iceland northern lights", reading(home="WONDER", kinds=["wonder"], places=["Iceland"]), ago(20)),
    ("Iceland flights in March", reading(home="WONDER", kinds=["wonder"], places=["Iceland"]), ago(12)),
    ("Iceland hot springs", reading(home="WONDER", kinds=["wonder"], places=["Iceland"]), ago(6)),
    ("Iceland ring road", reading(home="WONDER", kinds=["wonder"], places=["Iceland"]), ago(1)),
    ("Work was chaotic and I felt behind all day", reading(home="JOURNAL", kinds=["reflection"]), mondays_ago(0)),
    ("Felt behind again after the meeting marathon", reading(home="JOURNAL", kinds=["reflection"]), mondays_ago(1)),
    ("Still behind on the report", reading(home="JOURNAL", kinds=["reflection"]), mondays_ago(3)),
    ("Quiet evening, felt calm", reading(home="JOURNAL", kinds=["reflection"]), ago(4)),
    ("Boiler making the banging noise again", reading(kinds=["problem"], themes=["boiler"]), ago(30)),
    ("Boiler pressure dropped", reading(kinds=["problem"], themes=["boiler"]), ago(8)),
    ("Renew the passport", reading(home="NOW", kinds=["task"], intention="act",
                                   dates=[{"text": "Friday", "kind": "deadline", "iso": "2026-09-25"}]), ago(15)),
    ("Plan the garden", reading(home="NOW", kinds=["project"], intention="act"), ago(45)),
    ("Sort the loft", reading(home="NOW", kinds=["project"], intention="act"), ago(44)),
    ("An old thought about bread", reading(), ago(65)),
] + [(f"Filler thought number {i}", reading(), ago(3 * i)) for i in range(1, 7)]
texts = [t for t, _, _ in spec]
paths = make_notes(N, texts)
sorting.sort_inbox(StandIn({t: rd for t, rd, _ in spec}), {}, notes_dir=N, max_batches=10)
for p, (_, _, created) in zip(paths, spec):
    sc = rec_for(N, p); sc["created"] = created; records.save(sc, N)
recs = {s["path"]: s for s in records.load_all(N)[0].values()}
passport = rec_for(N, paths[texts.index("Renew the passport")])
loops.update_loop(passport["id"], "due", "2026-10-02", notes_dir=N)
loops.update_loop(passport["id"], "due", "2026-10-09", notes_dir=N)
for t in ("Plan the garden", "Sort the loft"):
    sc = rec_for(N, paths[texts.index(t)])
    loops.create_project(t.split()[-1].title(), [sc["id"]], notes_dir=N)
for sc in records.load_all(N)[0].values():            # age the projects' own records
    if sc.get("project_meta"):
        for h in sc.get("history", []): h["at"] = ago(40)
        sc["created"] = ago(40); records.save(sc, N)
U = tempfile.mkdtemp(prefix="jarvis-usage-")
os.makedirs(os.path.join(U, "reports", "weekly"))
for w, focus in (("2026-W36", 3), ("2026-W37", 5), ("2026-W38", 6)):
    json.dump({"tracked_seconds": 20 * 3600, "focus_seconds": focus * 3600,
               "entries": {"site:YouTube": {"kind": "site", "label": "YouTube", "seconds": 9000, "focus_seconds": 0},
                           "app:Word": {"kind": "app", "label": "Word", "seconds": 4000, "focus_seconds": 3000}}},
              open(os.path.join(U, "reports", "weekly", w + ".json"), "w"))
before = note_hashes(N)
rec_before = json.dumps(records.load_all(N)[0], sort_keys=True)

r = patterns.detect(N, today, usage_dir=U)
lines = {p["kind"]: [] for p in r["patterns"]}
for p in r["patterns"]: lines[p["kind"]].append(p["line"])
allt = " || ".join(p["line"] for p in r["patterns"])
check("ready (25 notes over 65 days)", r["ready"] and r["readiness"]["notes"] >= 20)
check("mentions: Dave, 4 notes on 4 days", any("Dave in 4 notes on 4 different days" in l for l in lines.get("mentions", [])))
check("rising: Iceland 1 -> 4", any(l.startswith("Iceland is coming up more often: 1 note in the 30 days before, 4 in the last 30")
                                    for l in lines.get("rising", [])))
check("feelings: 'behind' x3, all Mondays", "You've written 'behind' in 3 reflections. 3 of them on a Monday." in lines.get("feelings", []))
check("feelings: 'calm' once is not a pattern", "'calm'" not in allt)
check("problems: boiler twice", "You've noted boiler as a problem 2 times." in lines.get("problems", []))
check("slipping: passport moved twice", any("'Renew the passport' has had its date moved 2 times" in l for l in lines.get("slipping", [])))
check("stalled: 2 quiet projects", any(l.startswith("2 active projects have gone quiet") for l in lines.get("stalled", [])))
check("usage: focus time per week, counts only", "Focus-session time over the last 3 weeks: 3.0h, 5.0h, 6.0h (of 20.0h, 20.0h, 20.0h tracked)." in lines.get("usage", []))
check("usage: most-time label in 3 of 3 weeks", "YouTube took the most time in 3 of the last 3 weeks." in lines.get("usage", []))
check("filler notes produce nothing", "Filler" not in allt)

print("Test 3: evidence and wording")
ids = {s["id"] for s in records.load_all(N)[0].values()}
check("every pattern has evidence", all(p["evidence"] for p in r["patterns"]))
check("note evidence points at real records", all(e["id"] in ids for p in r["patterns"] for e in p["evidence"] if e["id"]))
fe = next(p for p in r["patterns"] if p["kind"] == "feelings")
by_id = {x["id"]: x for x in records.load_all(N)[0].values()}
check("feeling evidence notes all contain the word",
      all("behind" in open(records.abs_path(by_id[e["id"]]["path"], N), encoding="utf-8").read().lower() for e in fe["evidence"]))
check("no speculation words", not re.search(r"\b(because|probably|you tend|you always|means you|suggests)\b", allt, re.I))
check("spoken lists up to 3 and points at the evidence", r["spoken"].startswith("What the records show:")
      and r["spoken"].endswith("The evidence for each is listed."))

print("Test 4: where it shows up")
a = views.answer("Jarvis, what patterns do you see?", N, today)
check("/ask: 'what patterns do you see' -> patterns intent with evidence items", a["intent"] == "patterns" and a["items"])
for q in ("any patterns", "do you notice any patterns", "what do I keep coming back to"):
    check(f"router: '{q}'", views.classify(q)[0] == "patterns")
check("'what have i mentioned a lot' still goes to repeated", views.classify("what have i mentioned a lot")[0] == "repeated")
import usage_tracker
real_usage = usage_tracker.USAGE_DIR
usage_tracker.USAGE_DIR = U
ov = views.overview(N, today)
wk = reviews.build("weekly", N, today, {})
usage_tracker.USAGE_DIR = real_usage
sec = next((s for s in ov["life"] if s and s["title"] == "Patterns"), None)
check("overview: Patterns section, each line opens a note", sec and len(sec["items"]) >= 3)
ws = next((s for s in wk["sections"] if s["title"].startswith("Patterns")), None)
check("weekly review: Patterns section (max 3)", ws and 1 <= len(ws["items"]) <= 3)
ov_small = views.overview(S, today)
check("overview: no Patterns section when not enough data", not any(s and s["title"] == "Patterns" for s in ov_small["life"]))

print("Test 5: nothing changed")
check("notes byte-for-byte unchanged", note_hashes(N) == before)
check("detect() changed no records", json.dumps(records.load_all(N)[0], sort_keys=True) == rec_before)

for d in (S, N, U, EMPTY_USAGE): shutil.rmtree(d, ignore_errors=True)
print(f"\n{PASS} passed, {FAIL} failed")
