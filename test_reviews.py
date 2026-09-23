"""
Tests for Personal OS Phase 5 (2.7.0): reviews and "anything forgotten?".
Fixed dates, stand-in model, throwaway folders; your real notes are never touched.
Run: python3 test_reviews.py
"""
import datetime, json, os, re, shutil, subprocess, tempfile, threading, urllib.error, urllib.request
from http.server import ThreadingHTTPServer
import loops, records, reviews, server, sorting
from testkit import StandIn, reading, make_notes, note_hashes, rec_for

PASS = FAIL = 0
ROOT = os.path.dirname(os.path.abspath(__file__))


def check(label, ok):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
    print(f"  [{'OK' if ok else 'FAIL'}]{'   ' if ok else ' '}{label}")


D = datetime.datetime
S = dict(reviews.DEFAULT_SETTINGS)
reviews.SETTINGS_PATH = os.path.join(tempfile.mkdtemp(), "review_settings.json")   # never the real one

print("Test 1: when each review is offered (Wed 23 Sep 2026; Sunday is the 27th)")
N = tempfile.mkdtemp(prefix="jarvis-rev-")
st = reviews.load_state(N)
check("07:59: nothing", reviews.due_review(D(2026, 9, 23, 7, 59), S, st) is None)
check("08:00: morning", reviews.due_review(D(2026, 9, 23, 8, 0), S, st) == "morning")
check("19:00: evening", reviews.due_review(D(2026, 9, 23, 19, 0), S, st) == "evening")
check("Sunday 19:00: weekly (on top of evening)", reviews.due_review(D(2026, 9, 27, 19, 5), S, st) == "weekly")
reviews.mark("morning", "started", now=D(2026, 9, 23, 8, 5), notes_dir=N)
check("done once -> not offered again that day", reviews.due_review(D(2026, 9, 23, 12, 0), S, reviews.load_state(N)) is None)
check("...but offered the next morning", reviews.due_review(D(2026, 9, 24, 9, 0), S, reviews.load_state(N)) == "morning")
reviews.mark("evening", "later", now=D(2026, 9, 23, 19, 0), notes_dir=N)
check("'later' snoozes 30 minutes", reviews.due_review(D(2026, 9, 23, 19, 20), S, reviews.load_state(N)) is None
      and reviews.due_review(D(2026, 9, 23, 19, 31), S, reviews.load_state(N)) == "evening")
notes = [reviews.mark("evening", "skipped", now=D(2026, 9, 23 + i, 19, 1), notes_dir=N) for i in range(3)]
check("skipped 3 times in a row -> said once, plainly", notes[:2] == [None, None] and "stop popping it up" in notes[2])
check("...and its pop-ups go quiet", reviews.popups_quiet("evening", reviews.load_state(N)))
reviews.mark("evening", "started", now=D(2026, 9, 26, 19, 1), notes_dir=N)
check("doing one again brings the pop-ups back", not reviews.popups_quiet("evening", reviews.load_state(N)))
check("switched off -> nothing offered", reviews.due_review(D(2026, 9, 23, 9, 0), dict(S, enabled=False), {}) is None)

print("\nTest 2: settings")
sp = os.path.join(tempfile.mkdtemp(), "rs.json")
s, prob = reviews.load_settings(sp)
check("created with your choices: 08:00 / 19:00 / Sunday 19:00, pop-ups on",
      (s["morning"], s["evening"], s["weekly_day"], s["weekly_time"], s["popups"]) == ("08:00", "19:00", "Sunday", "19:00", True)
      and os.path.exists(sp))
open(sp, "w").write('{"morning": "8am"}')
s, prob = reviews.load_settings(sp)
check("a bad time -> defaults used, reason given, file left as it was",
      s["morning"] == "08:00" and "should look like 08:00" in prob and open(sp).read() == '{"morning": "8am"}')

print("\nTest 3: the pop-up (once, and quiet when asked)")
shown = []
fake = lambda title, text: (shown.append(text) or (True, None))
N2 = tempfile.mkdtemp(prefix="jarvis-rev2-")
for minute in (0, 1, 30):
    reviews.check_once(now=D(2026, 9, 23, 8, minute), notes_dir=N2, popup=fake)
check("popped once for the morning, not every minute", shown == ["Your morning review is ready. Open the Jarvis tab when you're ready."])
reviews.check_once(now=D(2026, 9, 23, 19, 0), notes_dir=N2, popup=fake)
check("then once for the evening", len(shown) == 2 and "evening" in shown[1])
open(reviews.SETTINGS_PATH, "w").write(json.dumps(dict(S, popups=False)))
reviews.check_once(now=D(2026, 9, 24, 8, 0), notes_dir=N2, popup=fake)
check("popups: false -> no pop-up", len(shown) == 2)
os.remove(reviews.SETTINGS_PATH)
fail = lambda title, text: (False, "toast failed")
reviews.check_once(now=D(2026, 9, 25, 8, 0), notes_dir=N2, popup=fail)
check("a failed pop-up is recorded for /diag, and not retried every minute",
      reviews.STATUS["popup_error"] == "toast failed" and reviews.load_state(N2)["popped"]["morning"] == "2026-09-25")
check("off Windows the real pop-up just says so", reviews.windows_popup("t", "x") == (False, "not Windows"))

print("\nTest 4: building reviews from real (stand-in-sorted) notes")
texts = ["Need to ring Dave by Friday about the van", "Waiting on Dave to send the quote",
         "Promised Sam I'd help him move on Saturday", "Mum's birthday party on Sunday",
         "Work was chaotic, should probably email Priya about it", "Pay the council tax today",
         "Dave mentioned the garage again", "Dave says the tyres are worn"]
paths = make_notes(N, texts)
before = note_hashes(N)
sorting.sort_inbox(StandIn({
    "ring Dave": reading(home="NOW", kinds=["task"], intention="act", people=["Dave"],
                         dates=[{"text": "Friday", "kind": "deadline", "iso": "2026-09-25"}]),
    "Waiting on": reading(home="NOW", kinds=["waiting_for"], intention="act", people=["Dave"]),
    "Promised Sam": reading(home="NOW", kinds=["commitment"], intention="act", people=["Sam"],
                            dates=[{"text": "Saturday", "kind": "deadline", "iso": "2026-09-26"}]),
    "birthday": reading(home="AREAS", kinds=["knowledge"], dates=[{"text": "Sunday", "kind": "event", "iso": "2026-09-27"}]),
    "chaotic": reading(home="JOURNAL", kinds=["reflection", "task"], intention="maybe", people=["Priya"]),
    "council tax": reading(home="NOW", kinds=["task"], intention="act",
                           dates=[{"text": "today", "kind": "deadline", "iso": "2026-09-23"}]),
    "garage": reading(home="KNOWLEDGE", people=["Dave"]), "tyres": reading(home="KNOWLEDGE", people=["Dave"]),
}), {}, notes_dir=N, today=datetime.date(2026, 9, 23))
sorting.decide_for_you(rec_for(N, paths[4])["id"], "move", home="JOURNAL", notes_dir=N)  # you filed it, still unsure if a task
today = datetime.date(2026, 9, 23)
m = reviews.build("morning", N, today=today)
titles = {s["title"]: [i["text"] for i in s["items"]] for s in m["sections"]}
check("morning: council tax is what matters today", any("council tax" in x and "due today" in x for x in titles["What matters today"]))
check("morning: Friday's call and Saturday's promise could become a problem",
      len(titles["What could become a problem"]) == 2)
check("morning: waiting on Dave's quote", titles["What you're waiting for"] == ["Waiting on Dave to send the quote"])
check("morning spoken: short, ends with the good-day question",
      m["spoken"] == "Good morning, sir. 1 due today, 2 coming up in the next few days, you're waiting on 1 thing. "
                     "What would make today a good day?")
loops.update_loop(rec_for(N, paths[5])["id"], "done", notes_dir=N)
e = reviews.build("evening", N, today=today)
et = {s["title"]: [i["text"] for i in s["items"]] for s in e["sections"]}
check("evening: what happened (captures + done)", "Done: Pay the council tax today" in et["What happened today"])
check("evening: the promise to Sam", any("Sam" in x for x in et["What you promised someone"]))
check("evening asks what's on your mind, with two questions", e["spoken"].endswith("What's still on your mind?")
      and e["questions"] == ["What's still on your mind?", "What was good about today?"])

later = datetime.date(2026, 10, 12)       # three weeks on, nothing done
f = reviews.build("forgotten", N, today=later)
ft = {s["title"]: [i["text"] for i in s["items"]] for s in f["sections"]}
check("forgotten: promise still open", any("Sam" in x for x in ft.get("Promises still open", [])))
check("forgotten: waiting with no follow-up (days counted)", any("quote" in x and "days)" in x for x in ft.get("Waiting, with no follow-up for a while", [])))
check("forgotten: said you'd do, not started", any("ring Dave" in x for x in ft.get("Said you'd do, not started yet", [])))
check("forgotten: a task possibly buried in a journal entry", any("chaotic" in x for x in ft.get("Possibly buried in a journal entry", [])))
f_soon = reviews.build("forgotten", N, today=datetime.date(2026, 9, 21))
fs = {s["title"]: [i["text"] for i in s["items"]] for s in f_soon["sections"]}
check("forgotten (Monday before): Mum's birthday coming up", any("birthday" in x for x in fs.get("Dates coming up", [])))
check("forgotten: Dave mentioned 3+ times... but he has loops, so not nagged about",
      "Mentioned a lot, nothing done with it yet" not in ft)
check("wording is plain, never accusatory", not re.search(r"\b(failed|should have|you forgot|neglect|lazy)\b",
                                                          json.dumps(ft).lower()))
for _ in range(3):
    reviews.note_raised(reviews.build("forgotten", N, today=later, state=reviews.load_state(N)), N)
fq = reviews.build("forgotten", N, today=later, state=reviews.load_state(N))
check("raised 3 times with nothing done -> one quiet 'still here' line, not repeated",
      fq["sections"][-1]["title"] == "Still here (raised before)" and len(fq["sections"]) == 1)
w = reviews.build("weekly", N, today=later)
wt = [s["title"] for s in w["sections"]]
check("weekly: overdue first, spoken line ends with its question",
      wt[0] == "Overdue" and w["spoken"].endswith("Anything from this week you want to remember?"))
check("NOTES UNCHANGED by building reviews", all(note_hashes(N)[k] == v for k, v in before.items()))

print("\nTest 5: your answers go to the Journal, word for word")
sc = reviews.save_answers("evening", [("What's still on your mind?", "The van, honestly. Fuck it."),
                                      ("What was good about today?", "  ")], source="spoken", notes_dir=N,
                          now=D(2026, 9, 23, 19, 30))
body = open(records.abs_path(sc["path"], N)).read()
check("one Journal note, your words exactly, the blank one left out",
      sc["path"] == "journal/2026-09-23-evening-review.md" and "**What's still on your mind?**\nThe van, honestly. Fuck it." in body
      and "What was good" not in body)
check("record: Journal, sorted, spoken", sc["home"] == "JOURNAL" and sc["status"] == "sorted" and sc["source"] == "spoken")
check("nothing answered -> nothing written", reviews.save_answers("morning", [("q", "")], notes_dir=N) is None)
records.sync_and_index(N)
check("records healthy", records.diag(N)["healthy"])

print("\nTest 6: over HTTP + the page's phrases")
W = tempfile.mkdtemp(prefix="jarvis-revhttp-")
real = {k: getattr(server, k) for k in ("NOTES_DIR", "GRAPH_DATA_PATH")}
real_run = server.subprocess.run
server.NOTES_DIR, server.GRAPH_DATA_PATH = N, os.path.join(W, "viewer", "graph-data.js")
os.makedirs(os.path.join(W, "viewer"))
server.subprocess.run = lambda cmd, cwd=None, **kw: real_run(cmd, cwd=W, **kw)
httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
PORT = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()
def get(p): return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}{p}", timeout=10).read())
def post(p, b): return json.loads(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}{p}",
    data=json.dumps(b).encode(), headers={"Content-Type": "application/json"}, method="POST"), timeout=20).read())
try:
    check("/reviews/due answers", "due" in get("/reviews/due"))
    r = get("/reviews/build?kind=morning")
    check("/reviews/build morning", r["ok"] and r["title"] == "Morning review" and r["questions"])
    check("/reviews/build forgotten", get("/reviews/build?kind=forgotten")["ok"])
    check("unknown review refused", not get("/reviews/build?kind=monthly")["ok"])
    check("/reviews/mark", post("/reviews/mark", {"kind": "morning", "action": "later"})["ok"])
    r = post("/reviews/save", {"kind": "morning", "answers": [{"question": "What would make today a good day?",
                                                                  "answer": "Getting the van sorted"}], "source": "typed"})
    check("/reviews/save -> journal", r["ok"] and r["saved"])
    try:
        post("/reviews/save", {"kind": "nope"}); refused = False
    except urllib.error.HTTPError as e:
        refused = e.code == 400
    check("bad body refused (HTTP 400)", refused)
finally:
    httpd.shutdown(); server.subprocess.run = real_run
    for k, v in real.items(): setattr(server, k, v)
    shutil.rmtree(W, ignore_errors=True)

html = open(os.path.join(ROOT, "viewer", "index.html"), encoding="utf-8").read()
bits = [re.search(r"const LEADING_FILLERS = \[.*?\];", html, re.S).group(0)]
bits += [re.search(rf"const {n} = /.*?/i;", html).group(0) for n in ("REVIEW_RE", "FORGOTTEN_RE")]
bits += [re.search(rf"function {f}\(text\) \{{.*?\n\}}", html, re.S).group(0) for f in ("stripLeadingFiller", "commandForm")]
phr = {"morning review": "morning", "Start my evening review.": "evening", "do the weekly review please": "weekly",
       "have I forgotten anything?": "forgotten", "is there anything I've forgotten": "forgotten",
       "what have I forgotten": "forgotten", "review my notes": None, "I forgot my keys": None}
js = "\n".join(bits) + f"""
console.log(JSON.stringify({json.dumps(list(phr))}.map(p => {{ const c = commandForm(p); const m = c.match(REVIEW_RE);
  return m ? m[4].toLowerCase() : FORGOTTEN_RE.test(c) ? 'forgotten' : null; }})));"""
res = subprocess.run(["node", "-e", js], capture_output=True, text=True)
got = json.loads(res.stdout) if res.returncode == 0 else []
for (p_, want), g in zip(phr.items(), got):
    check(f"page: {p_!r} -> {want}", g == want)
check("node ran", len(got) == len(phr))
shutil.rmtree(N, ignore_errors=True); shutil.rmtree(N2, ignore_errors=True)
print(f"\n{PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)
