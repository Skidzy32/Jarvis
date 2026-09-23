"""
Tests for Personal OS Phase 6 (2.8.0): asking Jarvis and the overview.
Fixed dates, stand-in model, throwaway folders; your real notes are never touched.
Run: python3 test_views.py
"""
import datetime, json, os, re, shutil, tempfile, threading, urllib.request
from http.server import ThreadingHTTPServer
import loops, records, server, sorting, views
from testkit import StandIn, reading, make_notes, note_hashes, rec_for

PASS = FAIL = 0


def check(label, ok):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
    print(f"  [{'OK' if ok else 'FAIL'}]{'   ' if ok else ' '}{label}")


print("Test 1: the spec's questions (§24) are recognised")
spec_qs = {
    "What am I doing?": "today", "What should I be thinking about today?": "today",
    "What am I waiting for?": None,            # handled by the page's own WAITING command (2.6.0)
    "What have I forgotten?": None,            # handled by the forgotten check (2.7.0)
    "What have I been putting off?": "putting_off", "What projects am I running?": "projects",
    "What did I decide about the van?": "decided", "Why did I decide that?": None,   # WHY command (2.6.0)
    "What is currently worrying me?": "worrying", "What have I said I want to do?": "wanted",
    "What have I mentioned repeatedly?": "repeated", "What is coming up?": "coming_up",
    "What should I probably deal with before it becomes a problem?": "before_problem",
    "What's happening at work?": "work", "What has changed recently?": "changed",
    "What have I learned recently?": "learned", "What am I interested in at the moment?": "interested",
    "What should I be excited about?": "excited", "What haven't I done that I said mattered to me?": "neglected",
    "Jarvis, what's going on in my life right now?": "life",
}
for q, want in spec_qs.items():
    check(f"{q!r} -> {want or 'another command'}", views.classify(q)[0] == want)
for q in ("what's the capital of France", "what do you think of my essay", "tell me a joke", "what am I doing wrong with Python"):
    check(f"not a stored-data question, goes to the brain: {q!r}", views.classify(q)[0] is None)

print("\nTest 2: answers come from your notes only")
N = tempfile.mkdtemp(prefix="jarvis-views-")
texts = ["Need to ring Dave by Friday about the van", "Waiting on Dave to send the quote",
         "Promised Sam I'd help him move on Saturday", "Mum's birthday party on Sunday",
         "Work was chaotic and I felt constantly behind", "Fix the leaking tap",
         "Would love to see the northern lights", "The boiler code is 4471",
         "Decided to go with the blue van because it's cheaper to insure", "Prepare the Q4 budget for work by Thursday",
         "Dave mentioned the garage again", "Dave says the tyres are worn", "Really into astronomy this week"]
paths = make_notes(N, texts)
before = note_hashes(N)
sorting.sort_inbox(StandIn({
    "ring Dave": reading(home="NOW", kinds=["task"], intention="act", people=["Dave"],
                         dates=[{"text": "Friday", "kind": "deadline", "iso": "2026-09-25"}]),
    "Waiting on": reading(home="NOW", kinds=["waiting_for"], intention="act", people=["Dave"]),
    "Promised Sam": reading(home="NOW", kinds=["commitment"], intention="act", people=["Sam"],
                            dates=[{"text": "Saturday", "kind": "deadline", "iso": "2026-09-26"}]),
    "birthday": reading(home="AREAS", kinds=["knowledge"], dates=[{"text": "Sunday", "kind": "event", "iso": "2026-09-27"}]),
    "chaotic": reading(home="JOURNAL", kinds=["reflection"], memory="current_state", temporary=True, area="Work"),
    "leaking tap": reading(home="NOW", kinds=["task"], intention="act", area="Home"),
    "northern lights": reading(home="WONDER", kinds=["wonder"], memory="interest"),
    "boiler code": reading(home="KNOWLEDGE", kinds=["knowledge"]),
    "Decided": reading(home="KNOWLEDGE", kinds=["decision"], decision={"what": "go with the blue van",
                       "why": ["it's cheaper to insure"], "alternatives": [], "constraints": [], "consequences": []}),
    "Q4 budget": reading(home="NOW", kinds=["task"], intention="act", area="Work",
                         dates=[{"text": "Thursday", "kind": "deadline", "iso": "2026-09-24"}]),
    "garage": reading(home="KNOWLEDGE", people=["Dave"]), "tyres": reading(home="KNOWLEDGE", people=["Dave"]),
    "astronomy": reading(home="WONDER", kinds=["wonder"], memory="interest", temporary=True),
}), {}, notes_dir=N)
today = datetime.date(2026, 9, 23)
titles = {records.title_of(p) for p in paths}
def ask(q, day=today):
    return views.answer(q, N, today=day)
def quoted_ok(a):
    """Every item an answer names is a real note, and its title is what's said."""
    ids = set(records.load_all(N)[0])
    return all(i["record_id"] in ids and i["title"] in titles
               and (i["title"] in a["spoken"] or a["intent"] == "decided")   # decisions quote the decision itself
               for i in a["items"] if i.get("record_id"))

a = ask("what should I be thinking about today")
check("today: the 3 most important, soonest first (Q4 budget Thu, Dave Fri, Sam Sat)",
      [i["title"][:12] for i in a["items"]] == ["Prepare the ", "Need to ring", "Promised Sam"] and quoted_ok(a))
a = ask("what's coming up?")
check("coming up: deadlines and the birthday, with days", "Sunday 27 September" in a["spoken"] and quoted_ok(a))
a = ask("what have I been putting off", day=datetime.date(2026, 10, 5))
check("putting off (two weeks on): open to-dos over a week old, said without judgement",
      len(a["items"]) == 4 and "No judgement" in a["spoken"])
check("putting off today: nothing yet", ask("what have I been putting off")["items"] == [])
a = ask("what's worrying me")
check("worrying: only your words (the chaotic day), and says so", a["items"][0]["title"].startswith("Work was chaotic")
      and "your words, not my conclusions" in a["spoken"])
a = ask("what have I said I want to do")
check("wanted: Wonder notes", {i["title"] for i in a["items"]} >= {"Would love to see the northern lights"})
check("repeated: Dave 4 times", ask("what have I mentioned repeatedly")["spoken"].startswith(
      "In the last three weeks you've mentioned Dave 4 times"))
a = ask("what did I decide about the van")
check("decided: your reason word for word", "\"it's cheaper to insure\"" in a["spoken"])
a = ask("what's happening at work")
check("work: only work items", [i["title"][:14] for i in a["items"]] == ["Prepare the Q4"])
check("before it becomes a problem: due within 5 days",
      len(ask("what should I deal with before it becomes a problem")["items"]) == 3)
a = ask("what am I interested in at the moment")
check("interests: Wonder + interests, and a passing interest isn't made permanent",
      len(a["items"]) == 2 and "passing thing" in a["spoken"])
check("excited: the birthday and the northern lights", "birthday" in ask("what should I be excited about")["spoken"].lower()
      and "northern lights" in ask("what should I be excited about")["spoken"])
check("learned: Knowledge notes", "boiler code" in ask("what have I learned recently")["spoken"])
loops.update_loop(rec_for(N, paths[5])["id"], "done", notes_dir=N)
check("changed: new notes and things done", "1 thing done" in ask("what's changed recently")["spoken"])
check("projects: none yet, said helpfully", ask("what projects am I running")["spoken"].startswith("No projects yet"))
check("neglected (a month on)", len(ask("what haven't I done that I said mattered to me", day=datetime.date(2026, 10, 30))["items"]) >= 2)
check("life overview answer", ask("what's going on in my life right now")["spoken"].startswith("4 open loops"))
check("every answer's quoted items are real notes", all(quoted_ok(ask(q)) for q in spec_qs if views.classify(q)[0]))
check("NOTES UNCHANGED by answering", all(note_hashes(N)[k] == v for k, v in before.items()))

print("\nTest 3: the overview (§26, §12) and work view (§11)")
ov = views.overview(N, today=today)
life = [s["title"] for s in ov["life"]]
check("life sections in order, empty ones left out",
      life[:5] == ["Today", "This week", "Open loops", "Waiting for", "Needs attention"] and "Wonder" in life)
check("Today has at most 3", len(ov["life"][0]["items"]) <= 3)
check("Recently captured and Recent decisions present", "Recently captured" in life and "Recent decisions" in life)
work = [s["title"] for s in ov["work"]]
check("work view: priorities + things to watch, only work", work[0] == "Current work priorities"
      and all("Q4" in i["text"] for i in ov["work"][0]["items"]))
E = tempfile.mkdtemp()
check("an empty notes folder: an empty overview, and an honest answer",
      views.overview(E)["empty"] and views.answer("what am I doing", E)["spoken"].startswith("There's nothing in your notes"))

print("\nTest 4: over HTTP")
W = tempfile.mkdtemp(prefix="jarvis-viewshttp-")
real = {k: getattr(server, k) for k in ("NOTES_DIR", "GRAPH_DATA_PATH")}
server.NOTES_DIR, server.GRAPH_DATA_PATH = N, os.path.join(W, "g.js")
httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
PORT = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()
def post(p, b): return json.loads(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}{p}",
    data=json.dumps(b).encode(), headers={"Content-Type": "application/json"}, method="POST"), timeout=20).read())
try:
    check("/ask answers a stored-data question", post("/ask", {"question": "what's coming up"})["intent"] == "coming_up")
    check("/ask hands anything else back (intent null)", post("/ask", {"question": "tell me a joke"}) == {"intent": None})
    check("/overview", "life" in json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/overview", timeout=10).read()))
finally:
    httpd.shutdown()
    for k, v in real.items(): setattr(server, k, v)
    shutil.rmtree(W, ignore_errors=True); shutil.rmtree(N, ignore_errors=True)
print(f"\n{PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)
