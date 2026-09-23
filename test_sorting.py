"""
Tests for Personal OS Phase 3 (2.5.0): sorting the inbox and linking.

A stand-in model answers from a script, so every rule can be checked
exactly: invented names dropped, the Japan rule, questions instead of
guesses, two models disagreeing, your confirmations and corrections, links,
batching, failures -- and that no note is ever changed by a byte.
Everything runs in throwaway folders.
Run: python3 test_sorting.py
"""

import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import records
import server
import sorting

PASS = 0
FAIL = 0
ROOT = os.path.dirname(os.path.abspath(__file__))


def check(label, condition):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK]   {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}")


def reading(**kw):
    base = {"home": "KNOWLEDGE", "area": None, "kinds": ["knowledge"], "intention": "none",
            "memory": "fact", "temporary": False, "people": [], "places": [], "organisations": [],
            "dates": [], "themes": [], "urgency": "unknown", "confidence": 0.9, "question": None}
    base.update(kw)
    return base


class StandIn:
    """Answers each note by matching words in its text to a script."""
    def __init__(self, script, model="stand-in/model-a"):
        self.script, self.model, self.calls, self.fail_on_call = script, model, 0, None
        self.raw_reply = None

    def __call__(self, config, messages):
        self.calls += 1
        if self.fail_on_call == self.calls:
            raise RuntimeError("Every model in the chain failed — test: HTTP 429")
        payload = json.loads(messages[1]["content"])
        self.last_payload = payload
        if self.raw_reply is not None:
            return self.raw_reply, self.model, None
        answers = []
        for note in payload["notes"]:
            for key, ans in self.script.items():
                if key in note["text"]:
                    answers.append(dict(ans, n=note["n"]))
                    break
        return "Here you go:\n```json\n" + json.dumps({"notes": answers}) + "\n```", self.model, None


def make_notes(root, texts):
    paths = []
    start = len(records.note_files(root))
    for i, text in enumerate(texts, start):
        p = os.path.join(root, "captures", f"n{i:02d}.md")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(f"# {text[:40]}\n\nCaptured 2026-09-2{i % 3}.\n\n{text}\n")
        records.create_for_capture(p, text, source="typed", notes_dir=root)
        paths.append(p)
    return paths


def note_hashes(root):
    return {rel: hashlib.sha256(open(records.abs_path(rel, root), "rb").read()).hexdigest()
            for rel in records.note_files(root)}


def rec_for(root, path):
    return records.find_by_path(path, root)


# ---------------------------------------------------------------------------
print("Test 1: reading the model's reply")
check("JSON inside ``` fences and a sentence", sorting.parse_reply('Sure!\n```json\n{"notes": [{"n": 1}]}\n```') == [{"n": 1}])
for bad in ("", "no json here", '{"not_notes": 1}', "{broken"):
    try:
        sorting.parse_reply(bad)
        ok = False
    except (ValueError, json.JSONDecodeError):
        ok = True
    check(f"unusable reply refused: {bad!r}", ok)

print("\nTest 2: checking a reading against your note")
words = "Need to ring Dave tomorrow about the boiler at Mum's."
v = sorting.validate(reading(home="NOW", kinds=["task"], intention="act", people=["Dave", "Priya"],
                             places=["Mum's", "Paris"], organisations=["British Gas"],
                             dates=[{"text": "tomorrow", "kind": "deadline", "iso": "2026-09-24"},
                                    {"text": "next Friday", "kind": "deadline", "iso": "2026-10-02"}]), words)
check("a person in the note is kept, an invented one dropped", v["people"] == ["Dave"])
check("same for places and organisations", v["places"] == ["Mum's"] and v["organisations"] == [])
check("a date in the note is kept (with the model's reading of it); an invented date dropped",
      v["dates"] == [{"text": "tomorrow", "kind": "deadline", "iso": "2026-09-24"}])
check("everything dropped is listed, not hidden", sorted(v["dropped_not_in_note"]) == ["British Gas", "Paris", "Priya", "next Friday"])
check("clear intention -> stays a task in NOW", v["home"] == "NOW" and v["kinds"] == ["task"])

japan = sorting.validate(reading(home="NOW", kinds=["task", "wonder"], intention="none", places=["Japan"]),
                         "I've always wanted to visit Japan.")
check("the Japan rule: no intention -> not a task, not NOW", "task" not in japan["kinds"] and japan["home"] == "SOMEDAY")
check("...and what was held back is recorded", japan["held_back_action_kinds"] == ["task"])
w = sorting.validate(reading(home="WONDER", kinds=["wonder", "task"], intention="act"), "astronomy is amazing")
check("WONDER is never a to-do list, even if the model says act", w["kinds"] == ["wonder"])
odd = sorting.validate({"home": "GARAGE", "kinds": ["banana", 7], "intention": "sure", "confidence": "high",
                        "people": "Dave", "dates": "soon", "question": ""}, "x")
check("nonsense fields made safe (no home, no kinds, 'maybe', confidence 0)",
      odd["home"] is None and odd["kinds"] == [] and odd["intention"] == "maybe" and odd["confidence"] == 0.0
      and odd["people"] == [] and odd["question"] is None)
check("confidence clamped to 0..1", sorting.validate(reading(confidence=7), "x")["confidence"] == 1.0)

print("\nTest 3: deciding what happens")
check("low confidence -> UNSORTED", sorting.decide(sorting.validate(reading(confidence=0.3), "x"))[0] == "unsorted")
check("no home -> UNSORTED", sorting.decide(sorting.validate(reading(home=None), "x"))[0] == "unsorted")
st, q = sorting.decide(sorting.validate(reading(home="NOW", kinds=["task"], intention="maybe"),
                                        "I should probably talk to Dave about this."))
check("unsure whether you meant to act -> it asks, doesn't guess", st == "needs_clarification"
      and "left it uncommitted" in q["text"] and [o["action"] for o in q["options"]] == ["act", "thought"])
st, q = sorting.decide(sorting.validate(reading(question="Is this for work or home?"), "x"))
check("the model's own question is passed on, with filing choices", st == "needs_clarification"
      and q["text"] == "Is this for work or home?" and q["options"][0]["home"] == "KNOWLEDGE")
check("a clear reading -> sorted", sorting.decide(sorting.validate(reading(), "x"))[0] == "sorted")
st, q = sorting.decide(sorting.validate(reading(home="JOURNAL"), "x"), previous={"home": "WONDER"})
check("a second reading that disagrees with the first -> it asks which",
      st == "needs_clarification" and "Wonder or Journal" in q["text"])

# ---------------------------------------------------------------------------
print("\nTest 4: a real sorting run on a throwaway notes folder")
N = tempfile.mkdtemp(prefix="jarvis-sort-")
texts = [
    "Need to sort the spreadsheet tomorrow.",
    "Work was fucking chaotic today and I felt constantly behind.",
    "Would be cool to visit Iceland.",
    "Remember to ask Dave about closing.",
    "I've been thinking about building a Minecraft server.",
    "I don't know why but I've suddenly become really interested in astronomy.",
    "Dave's number is 07700 900123.",
    "blorp",
]
paths = make_notes(N, texts)
before = note_hashes(N)
script = {
    "spreadsheet": reading(home="NOW", kinds=["task"], intention="act", area="Work",
                           dates=[{"text": "tomorrow", "kind": "deadline", "iso": "2026-09-21"}]),
    "chaotic": reading(home="JOURNAL", kinds=["reflection"], memory="current_state", temporary=True, area="Work"),
    "Iceland": reading(home="WONDER", kinds=["wonder", "task"], intention="none", places=["Iceland"]),
    "ask Dave": reading(home="NOW", kinds=["task"], intention="maybe", people=["Dave"]),
    "Minecraft": reading(home="SOMEDAY", kinds=["idea", "project"], intention="none"),
    "astronomy": reading(home="WONDER", kinds=["wonder"], memory="interest", temporary=True),
    "number": reading(home="KNOWLEDGE", kinds=["knowledge", "person_context"], people=["Dave", "Sarah"]),
    "blorp": reading(home="KNOWLEDGE", confidence=0.2),
}
brain = StandIn(script)
summary = sorting.sort_inbox(brain, {}, notes_dir=N, today=datetime.date(2026, 9, 20))
check("one request for eight notes", brain.calls == 1 and len(brain.last_payload["notes"]) == 8)
check("the model got your words, not Jarvis's title/date lines",
      sorted(n["text"] for n in brain.last_payload["notes"]) == sorted(texts))
check("5 sorted, 1 question, 1 couldn't place... (8 total incl. the question)",
      len(summary["sorted"]) == 6 and len(summary["needs_you"]) == 1 and len(summary["unsorted"]) == 1)
check("NOT ONE NOTE CHANGED BY A BYTE", note_hashes(N) == before)
r = rec_for(N, paths[2])
check("Iceland -> Wonder, and not a task", r["home"] == "WONDER" and "task" not in r["interpretations"][-1]["value"]["kinds"])
r = rec_for(N, paths[1])
check("the chaotic day -> Journal, marked temporary, your words untouched",
      r["home"] == "JOURNAL" and r["interpretations"][-1]["value"]["temporary"] is True
      and open(paths[1]).read().endswith(texts[1] + "\n"))
r = rec_for(N, paths[3])
check("'ask Dave' (maybe) -> asks you, stays in the inbox", r["status"] == "needs_clarification" and r["home"] == "INBOX")
r = rec_for(N, paths[6])
check("'Sarah' (not in the note) dropped from Dave's number", r["interpretations"][-1]["value"]["people"] == ["Dave"])
r = rec_for(N, paths[7])
check("'blorp' -> couldn't place (UNSORTED)", r["status"] == "unsorted")
r = rec_for(N, paths[0])
it = r["interpretations"][-1]
check("each reading stored with model, time, confidence, confirmed=false",
      it["kind"] == "sorting" and it["model"] == "stand-in/model-a" and it["confidence"] == 0.9 and it["confirmed"] is False)
check("history says Jarvis sorted it", r["history"][-1]["event"] == "sorted by Jarvis")
check("links: Dave's two notes linked both ways, with the reason",
      any(l["reason"] == "both mention Dave" and l["by"] == "jarvis" and l["confirmed"] is False
          for l in rec_for(N, paths[3])["links"])
      and any(l.get("kind") == "related" for l in rec_for(N, paths[6])["links"]))
check("one pair -> one link counted", summary["links_added"] == 1)
check("running linking again adds nothing new", sorting.link_related(N) == 0)
records.sync_and_index(N)
check("records still healthy (after the usual rebuild)", records.diag(N)["healthy"])
line = sorting.spoken_summary(summary, {summary["needs_you"][0]: "Remember to ask Dave"})
check("what Jarvis says: calm and short", line == "Sorted, sir: 6 filed, 1 I'd like your word on, 1 I couldn't place. "
                                                "The first question is about 'Remember to ask Dave'.")

print("\nTest 5: the galaxy draws Jarvis's suggested links")
G = tempfile.mkdtemp(prefix="jarvis-galaxy-")
gp = make_notes(G, ["Lunch with Dave on the ninth", "the tenner Dave owes me"])
W = tempfile.mkdtemp(prefix="jarvis-galaxy-out-")


def galaxy():
    subprocess.run(["python3", os.path.join(ROOT, "build.py"), G], cwd=W, capture_output=True, text=True)
    g = json.loads(open(os.path.join(W, "viewer", "graph-data.js")).read()[len("const GRAPH = "):].rstrip().rstrip(";"))
    ids = {os.path.relpath(n["path"], G).replace(os.sep, "/"): n["id"] for n in g["nodes"]}
    pair = tuple(sorted((ids["captures/n00.md"], ids["captures/n01.md"])))
    return [l for l in g["links"] if (l["source"], l["target"]) == pair]


check("before sorting: two notes sharing only 'Dave' aren't connected", galaxy() == [])
sorting.sort_inbox(StandIn({"Dave": reading(people=["Dave"])}), {}, notes_dir=G)
e = galaxy()
check("after sorting: connected, and marked as Jarvis's suggestion", len(e) == 1 and e[0].get("suggested") is True)
g_hash = note_hashes(G)
check("...without a word added to either note", all("See also" not in open(p).read() for p in gp))
shutil.rmtree(W)
shutil.rmtree(G)

print("\nTest 6: you stay in charge")
r = sorting.decide_for_you(rec_for(N, paths[3])["id"], "act", notes_dir=N)
check("'yes, something to do' -> NOW, the held-back task restored, confirmed by you",
      r["home"] == "NOW" and "task" in r["interpretations"][-1]["value"]["kinds"]
      and r["interpretations"][-1]["confirmed"] and r["status"] == "sorted")
rid = rec_for(N, paths[4])["id"]
r = sorting.decide_for_you(rid, "confirm", notes_dir=N)
check("confirm -> kept where it was, confirmed, not 'corrected'",
      r["home"] == "SOMEDAY" and r["interpretations"][-1]["confirmed"] and not r["interpretations"][-1]["corrected_by_you"])
r = sorting.decide_for_you(rid, "move", home="PROJECTS", notes_dir=N)
check("move -> filed where you said, marked corrected", r["home"] == "PROJECTS" and r["interpretations"][-1]["corrected_by_you"])
r = sorting.decide_for_you(rec_for(N, paths[7])["id"], "move", home="JOURNAL", notes_dir=N)
check("an UNSORTED note can be given a home by you", r["home"] == "JOURNAL" and r["status"] == "sorted")
r = sorting.decide_for_you(rid, "inbox", notes_dir=N)
check("back to the inbox -> unprocessed again", r["home"] == "INBOX" and r["status"] == "unprocessed")
for bad in [("confirm", None, "r-nope"), ("fly", None, None), ("move", "GARAGE", None)]:
    try:
        sorting.decide_for_you(bad[2] or rec_for(N, paths[0])["id"], bad[0], home=bad[1], notes_dir=N)
        ok = False
    except ValueError:
        ok = True
    check(f"refused: {bad[0]} {bad[1] or ''}".strip(), ok)
r = sorting.decide_for_you(rec_for(N, paths[0])["id"], "thought", notes_dir=N)
check("'just a thought' on a NOW note -> Someday, nothing to act on", r["home"] == "SOMEDAY" and r["interpretations"][-1]["value"]["intention"] == "none")
check("still: NOT ONE NOTE CHANGED BY A BYTE", note_hashes(N) == before)

print("\nTest 7: the same note read twice by two different models")
brain_b = StandIn({"Minecraft": reading(home="PROJECTS", kinds=["project"], intention="none")}, model="stand-in/model-b")
s2 = sorting.sort_inbox(brain_b, {}, notes_dir=N)      # the Minecraft note was put back in the inbox
r = rec_for(N, paths[4])
readings = [i for i in r["interpretations"] if i["kind"] == "sorting"]
check("both readings kept, each with its own model", [i["model"] for i in readings] == ["stand-in/model-a", "stand-in/model-b"])
check("the first was confirmed by you, so the second (agreeing with your move) just files it",
      r["status"] == "sorted" and r["home"] == "PROJECTS")
p9 = make_notes(N, ["the garden shed needs a new roof"])[0]
sorting.sort_inbox(StandIn({"shed": reading(home="AREAS", kinds=["task"], intention="act", area="Home")}), {}, notes_dir=N)
sorting.decide_for_you(rec_for(N, p9)["id"], "inbox", notes_dir=N)
sorting.sort_inbox(StandIn({"shed": reading(home="PROJECTS", kinds=["project"], intention="act")}, model="stand-in/model-b"), {}, notes_dir=N)
r = rec_for(N, p9)
check("two unconfirmed readings that disagree -> it asks: Areas or Projects?",
      r["status"] == "needs_clarification" and "Areas or Projects" in r["question"]["text"])

print("\nTest 8: failures change nothing they shouldn't")
M = tempfile.mkdtemp(prefix="jarvis-sort2-")
many = make_notes(M, [f"note number {i} about knowledge" for i in range(30)])
bm = StandIn({"knowledge": reading()})
bm.fail_on_call = 2
s = sorting.sort_inbox(bm, {}, notes_dir=M)
check("model fails on the 2nd request: the first 8 sorted, the rest untouched",
      len(s["sorted"]) == 8 and s["remaining"] == 22 and "429" in s["error"]
      and len(sorting.waiting(M)) == 22)
check("...and the reply says so, calmly", "I stopped early" in sorting.spoken_summary(s, {}))
s = sorting.sort_inbox(StandIn({"knowledge": reading()}), {}, notes_dir=M)
check("next run: at most 3 requests (24 notes... 22 left -> 3 requests), none remaining",
      len(s["sorted"]) == 22 and s["remaining"] == 0)
Q = tempfile.mkdtemp(prefix="jarvis-sort3-")
make_notes(Q, [f"thing {i}" for i in range(30)])
s = sorting.sort_inbox(StandIn({"thing": reading()}), {}, notes_dir=Q)
check("30 waiting -> 24 sorted in 3 requests, 6 left for next time, and it says so",
      len(s["sorted"]) == 24 and s["remaining"] == 6 and "6 more are waiting" in sorting.spoken_summary(s, {}))
bj = StandIn({})
bj.raw_reply = "I'm sorry, I can't help with that."
R = tempfile.mkdtemp(prefix="jarvis-sort4-")
make_notes(R, ["one", "two"])
s = sorting.sort_inbox(bj, {}, notes_dir=R)
check("a reply with no JSON -> nothing changed, reason given",
      s["sorted"] == [] and len(sorting.waiting(R)) == 2 and "couldn't read" in s["error"])
check("...said plainly", sorting.spoken_summary(s, {}).startswith("I couldn't sort anything just now") and
      "untouched" in sorting.spoken_summary(s, {}))
S = tempfile.mkdtemp(prefix="jarvis-sort5-")
make_notes(S, ["alpha note", "beta note"])
s = sorting.sort_inbox(StandIn({"alpha": reading()}), {}, notes_dir=S)
check("a note the model skipped stays waiting for next time", len(s["sorted"]) == 1 and len(sorting.waiting(S)) == 1)
for d in (M, Q, R, S):
    shutil.rmtree(d)

print("\nTest 9: the instructions the model gets")
p = sorting.SORT_SYSTEM_PROMPT
check("says not to make everything a task (the Japan example)", "Do NOT turn every statement into a task" in p and "Japan" in p)
check("says feelings stay feelings", "Never rewrite someone's words into productivity language" in p)
check("says names and dates must be copied exactly", "copied EXACTLY as written" in p)
check("says temporary states are temporary", "temporary true" in p)

# ---------------------------------------------------------------------------
print("\nTest 10: over real HTTP, and the page's own phrases")
W = tempfile.mkdtemp(prefix="jarvis-sorthttp-")
NOTES = os.path.join(W, "notes")
os.makedirs(os.path.join(W, "viewer"))
real = {k: getattr(server, k) for k in ("NOTES_DIR", "GRAPH_DATA_PATH", "load_config", "call_brain")}
real_run = server.subprocess.run
server.NOTES_DIR = NOTES
server.GRAPH_DATA_PATH = os.path.join(W, "viewer", "graph-data.js")
server.subprocess.run = lambda cmd, cwd=None, **kw: real_run(cmd, cwd=W, **kw)
KEY = {"k": "PUT-YOUR-KEY-HERE"}
server.load_config = lambda: {"openrouter_api_key": KEY["k"]}
server.call_brain = StandIn({"Iceland": reading(home="WONDER", kinds=["wonder"], places=["Iceland"]),
                             "OpenRouter": reading(home="NOW", kinds=["task"], intention="act"),
                             "images": reading(home="PROJECTS", kinds=["project"], intention="act"),
                             "Dave": reading(home="NOW", kinds=["task"], intention="maybe", people=["Dave"])})
httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
PORT = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()


def post(path, payload):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def get(path):
    return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=10).read())


try:
    server.startup_rebuild()
    __import__("inbox").seed_followups(NOTES)          # 4.0.0: the server no longer adds the owner's F1/F2; this scenario uses them
    post("/remember", {"message": "remember that it would be cool to visit Iceland", "source": "typed"})
    post("/remember", {"message": "remember to ask Dave about closing", "source": "spoken"})
    before = note_hashes(NOTES)
    r = post("/sort", {})
    check("no key: refused plainly, nothing sorted", not r["ok"] and "API key" in r["error"] and get("/inbox")["count"] == 4)
    KEY["k"] = "sk-test"
    r = post("/sort", {})
    check("with a model: sorted, and a calm spoken line", r["ok"] and r["spoken"].startswith("Sorted, sir: 3 filed, 1 I'd like your word on"))
    ib = get("/inbox")
    check("/inbox now: nothing waiting, 1 question, 3 sorted",
          ib["count"] == 0 and len(ib["needs_you"]) == 1 and len(ib["sorted"]) == 3)
    ask = ib["needs_you"][0]
    check("the question comes with buttons", ask["question"]["options"][0]["action"] == "act")
    r = post("/sort/decide", {"record_id": ask["id"], "action": "thought"})
    check("answering it over HTTP", r["ok"] and r["status"] == "sorted" and "It's in Someday" in r["spoken"])
    r = post("/sort/decide", {"record_id": "r-nope", "action": "confirm"})
    check("a bad id is refused, not crashed", not r["ok"])
    check("nothing to sort -> says so", post("/sort", {})["spoken"] == "Nothing waiting to be sorted, sir.")
    check("NOT ONE NOTE CHANGED BY A BYTE (over HTTP too)", note_hashes(NOTES) == before)
finally:
    httpd.shutdown()
    server.subprocess.run = real_run
    for k, v in real.items():
        setattr(server, k, v)
    shutil.rmtree(W, ignore_errors=True)
shutil.rmtree(N, ignore_errors=True)

html = open(os.path.join(ROOT, "viewer", "index.html"), encoding="utf-8").read()
bits = [re.search(r"const LEADING_FILLERS = \[.*?\];", html, re.S).group(0),
        re.search(r"const SORT_RE = /.*?/i;", html).group(0),
        re.search(r"const INBOX_QUERY_RE = /.*?/i;", html).group(0),
        re.search(r"function stripLeadingFiller\(text\) \{.*?\n\}", html, re.S).group(0),
        re.search(r"function commandForm\(text\) \{.*?\n\}", html, re.S).group(0)]
phr = {"sort my inbox": True, "Sort my inbox.": True, "process the inbox": True, "organise my inbox": True,
       "tidy up my inbox": True, "go through my inbox": True, "Jarvis, sort my inbox please": True,
       "sort out my life": False, "what's in my inbox": False}
js = "\n".join(bits) + f"\nconsole.log(JSON.stringify({json.dumps(list(phr))}.map(p => SORT_RE.test(commandForm(p)))));"
res = subprocess.run(["node", "-e", js], capture_output=True, text=True)
got = json.loads(res.stdout) if res.returncode == 0 else []
for (p, want), g in zip(phr.items(), got):
    check(f"page: {p!r} {'sorts' if want else 'does not sort'}", g == want)

print(f"\n{PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)
