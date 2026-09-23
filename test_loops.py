"""
Tests for Personal OS Phase 4 (2.6.0): open loops, projects, decisions.
Stand-in model, throwaway folders; your real notes are never touched.
Run: python3 test_loops.py
"""
import datetime, json, os, re, shutil, subprocess, tempfile, threading, urllib.request
from http.server import ThreadingHTTPServer
import loops, records, server, sorting
from testkit import StandIn, reading, make_notes, note_hashes, rec_for

PASS = FAIL = 0
ROOT = os.path.dirname(os.path.abspath(__file__))


def check(label, ok):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
    print(f"  [{'OK' if ok else 'FAIL'}]{'   ' if ok else ' '}{label}")


print("\nLOOPS Test 1: sorting opens loops only for unresolved things")
N = tempfile.mkdtemp(prefix="jarvis-loops-")
texts = ["Need to ring Dave by Friday about the van", "Waiting on Dave to send the quote",
         "Dave said the van needs new tyres, must book it", "I'd love to visit Japan",
         "Decided to go with the blue van because it's cheaper to insure, rather than the red one",
         "Not sure whether to take the new job", "Work was chaotic today"]
paths = make_notes(N, texts)
before = note_hashes(N)
brain = StandIn({
    "ring Dave": reading(home="NOW", kinds=["task"], intention="act", people=["Dave"],
                         dates=[{"text": "Friday", "kind": "deadline", "iso": "2026-09-25"}]),
    "Waiting on": reading(home="NOW", kinds=["waiting_for"], intention="act", people=["Dave"]),
    "tyres": reading(home="NOW", kinds=["task"], intention="act", people=["Dave"]),
    "Japan": reading(home="WONDER", kinds=["wonder", "task"], intention="none"),
    "Decided": reading(home="KNOWLEDGE", kinds=["decision"], decision={
        "what": "go with the blue van", "why": ["it's cheaper to insure", "my mate recommended it"],
        "alternatives": ["the red one"], "constraints": [], "consequences": []}),
    "new job": reading(home="NOW", kinds=["decision_to_confirm"], intention="maybe"),
    "chaotic": reading(home="JOURNAL", kinds=["reflection"]),
})
sorting.sort_inbox(brain, {}, notes_dir=N, today=datetime.date(2026, 9, 23))
ov = loops.overview(N, today=datetime.date(2026, 9, 23))
kinds = {l["title"]: l["kind"] for l in ov["loops"]}
check("to do / waiting / to decide opened", sorted(kinds.values()) == ["to decide", "to do", "to do", "waiting"])
check("the wish (Japan) and the feeling aren't loops", not any("Japan" in t or "chaotic" in t for t in kinds))
ring = next(l for l in ov["loops"] if "ring Dave" in l["title"])
check("a deadline written in the note becomes the due date", ring["due"] == "2026-09-25")
check("soonest first", ov["loops"][0]["id"] == ring["id"])
check("not overdue yet; overdue after the date",
      not ring["overdue"] and next(l for l in loops.overview(N, today=datetime.date(2026, 9, 30))["loops"] if l["id"] == ring["id"])["overdue"])
check("spoken summary", loops.spoken_loops(ov).startswith("4 open loops, sir: 2 to do, 1 waiting, 1 to decide."))
check("'what am I waiting on'", loops.spoken_waiting(ov) == "You're waiting on 1 thing: 'Waiting on Dave to send the quote'.")
check("3 loops mentioning Dave -> 'combine them?'", len(ov["suggestions"]) == 1
      and ov["suggestions"][0]["question"] == "These 3 look like one project. They all mention Dave. Combine them?")
check("NOTES UNCHANGED", note_hashes(N) == before)

print("\nLOOPS Test 2: decisions keep only your own words")
d = rec_for(N, paths[4])["interpretations"][-1]["value"]["decision"]
check("reason in the note kept; invented reason dropped", d["why"] == ["it's cheaper to insure"])
check("alternative kept", d["alternatives"] == ["the red one"])
spoken, rid = loops.why("why did I decide on the blue van?", N)
check("'why did I decide...' answered word for word from the note",
      "you decided: \"go with the blue van\"" in spoken and "\"it's cheaper to insure\"" in spoken
      and "\"the red one\"" in spoken and "mate" not in spoken)
check("unknown decision -> says so, invents nothing", loops.why("why did I decide to move to Spain", N)[1] is None)
check("a decision whose 'what' isn't a quote falls back to the whole note",
      loops.check_decision({"what": "buy a boat", "why": []}, "we chose the train")["what"] == "we chose the train")
nowhy = loops.check_decision({"what": "we chose the train", "why": ["it is faster"]}, "we chose the train")
check("no reason written -> none recorded", nowhy["why"] == [])

print("\nLOOPS Test 3: you close, drop, date, and reopen")
loops.update_loop(ring["id"], "due", due="2026-10-01", notes_dir=N)
check("your date wins", rec_for(N, paths[0])["loop"]["due"] == "2026-10-01")
loops.update_loop(ring["id"], "done", notes_dir=N)
check("done -> gone from open loops", all(l["id"] != ring["id"] for l in loops.overview(N)["loops"]))
sorting.decide_for_you(ring["id"], "confirm", notes_dir=N)
check("re-confirming the note doesn't reopen a loop you closed", rec_for(N, paths[0])["loop"]["state"] == "done")
loops.update_loop(ring["id"], "reopen", notes_dir=N)
check("reopen", rec_for(N, paths[0])["loop"]["state"] == "open")
for bad in [("due", "Friday"), ("fly", None)]:
    try:
        loops.update_loop(ring["id"], bad[0], due=bad[1], notes_dir=N); ok = False
    except ValueError:
        ok = True
    check(f"refused: {bad[0]} {bad[1] or ''}", ok)
tyres = next(l for l in ov["loops"] if "tyres" in l["title"])
sorting.decide_for_you(tyres["id"], "thought", notes_dir=N)
check("'just a thought' on a to-do closes Jarvis's loop", rec_for(N, paths[2])["loop"]["state"] == "dropped")

print("\nLOOPS Test 4: projects")
ids = [l["id"] for l in loops.overview(N)["loops"] if "Dave" in l["title"]]
p = loops.create_project("The van", ids, outcome="Van road-legal and insured", notes_dir=N)
body = open(records.abs_path(p["path"], N)).read()
check("a project note is written (a new note, not an edit)", p["path"].startswith("projects/") and "# The van" in body
      and "Outcome: Van road-legal and insured" in body)
ov = loops.overview(N)
proj = ov["projects"][0]
check("project: outcome, active, open loops, next action = soonest open loop",
      proj["outcome"] == "Van road-legal and insured" and proj["status"] == "active"
      and proj["open_loops"] == len(ids) and proj["next_action"] == ov["loops"][0]["title"])
check("in a project -> no longer suggested", ov["suggestions"] == [])
loops.update_project(p["id"], notes_dir=N, status="paused", target="2026-11-01")
check("status + target date", loops.overview(N)["projects"][0]["status"] == "paused"
      and loops.overview(N)["projects"][0]["target"] == "2026-11-01")
for bad in [dict(status="someday"), dict(target="soon")]:
    try:
        loops.update_project(p["id"], notes_dir=N, **bad); ok = False
    except ValueError:
        ok = True
    check(f"refused: {bad}", ok)
records.sync_and_index(N)
check("records healthy; old notes unchanged",
      records.diag(N)["healthy"] and all(note_hashes(N)[k] == v for k, v in before.items()))

print("\nLOOPS Test 5: over HTTP + the page's phrases")
W = tempfile.mkdtemp(prefix="jarvis-loopshttp-")
real = {k: getattr(server, k) for k in ("NOTES_DIR", "GRAPH_DATA_PATH")}
real_run = server.subprocess.run
server.NOTES_DIR, server.GRAPH_DATA_PATH = N, os.path.join(W, "viewer", "graph-data.js")
os.makedirs(os.path.join(W, "viewer"))
server.subprocess.run = lambda cmd, cwd=None, **kw: real_run(cmd, cwd=W, **kw)
httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
PORT = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()
def get(path): return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=10).read())
def post(path, payload):
    return json.loads(urllib.request.urlopen(urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST"), timeout=20).read())
try:
    d = get("/loops")
    check("/loops: loops, projects, spoken", d["loops"] and d["projects"] and d["spoken"].startswith(f"{len(d['loops'])} open loop"))
    check("/decisions/why", "blue van" in get("/decisions/why?q=why%20did%20I%20decide%20on%20the%20van")["spoken"])
    r = post("/loops/update", {"record_id": d["loops"][0]["id"], "action": "done"})
    check("/loops/update done", r["ok"] and r["spoken"].startswith("Done, sir"))
    check("bad request refused, not crashed", not post("/loops/update", {"record_id": "r-no", "action": "done"})["ok"])
    check("/projects/update", post("/projects/update", {"project_id": p["id"], "status": "active"})["ok"])
finally:
    httpd.shutdown(); server.subprocess.run = real_run
    for k, v in real.items(): setattr(server, k, v)
    shutil.rmtree(W, ignore_errors=True); shutil.rmtree(N, ignore_errors=True)

html = open(os.path.join(ROOT, "viewer", "index.html"), encoding="utf-8").read()
bits = [re.search(r"const LEADING_FILLERS = \[.*?\];", html, re.S).group(0)]
bits += [re.search(rf"const {n} = /.*?/i;", html).group(0) for n in ("LOOPS_RE", "WAITING_RE", "WHY_RE", "SORT_RE", "INBOX_QUERY_RE")]
bits += [re.search(rf"function {f}\(text\) \{{.*?\n\}}", html, re.S).group(0) for f in ("stripLeadingFiller", "commandForm")]
phr = {"what are my open loops": "loops", "What's on my plate?": "loops", "what do I still need to do": "loops",
       "show me my projects": "loops", "loops": "loops", "what am I waiting on": "waiting",
       "Why did I decide to buy the van?": "why", "why did I go with the blue one": "why",
       "what's on the telly": None, "why is the sky blue": None, "sort my inbox": "sort"}
js = "\n".join(bits) + f"""
console.log(JSON.stringify({json.dumps(list(phr))}.map(p => {{ const c = commandForm(p);
  return LOOPS_RE.test(c) ? 'loops' : WAITING_RE.test(c) ? 'waiting' : WHY_RE.test(c) ? 'why' : SORT_RE.test(c) ? 'sort' : null; }})));"""
res = subprocess.run(["node", "-e", js], capture_output=True, text=True)
got = json.loads(res.stdout) if res.returncode == 0 else []
for (p_, want), g in zip(phr.items(), got):
    check(f"page: {p_!r} -> {want}", g == want)
check("node ran", len(got) == len(phr))

print(f"\n{PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)
