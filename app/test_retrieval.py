"""
4.6.0 (Jarvis 5.0 Phase 2): finding the right notes -- exact, word forms,
whole-note words, similar words, names, links, category, decisions, current
vs historical, importance -- and "what do I currently prefer / what did I
previously think". Stand-in model, throwaway folders; your real notes are
never touched. Run: python3 test_retrieval.py
"""
import json, os, shutil, tempfile, threading, time, urllib.parse, urllib.request
from http.server import ThreadingHTTPServer
import knowledge, links, loops, organise, records, retrieval, server, sorting
from testkit import StandIn, reading, make_notes, note_hashes, rec_for

PASS = FAIL = 0


def check(label, ok):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
    print(f"  [{'OK' if ok else 'FAIL'}]{'   ' if ok else ' '}{label}")


N = tempfile.mkdtemp(prefix="jarvis-retr-")
long_tail = " ".join(["Lots of detail about the day."] * 40)
texts = [
    "Van insurance renewal is due on the 1st of October",                   # 0
    "Invoices from Lumen Bakery need paying this week",                     # 1
    "Dave recommended the garage on Mill Lane for the service",             # 2
    "Decided to keep the van another year because repairs are cheap",       # 3
    "I prefer tea in the mornings",                                          # 4
    "These days I prefer coffee in the mornings",                           # 5
    "Ran 5k this morning, felt great",                                       # 6
    "Trip notes " + long_tail + " The passport expires in March.",           # 7
    "Mum's birthday is on the 3rd of May",                                   # 8
    "Budget for the kitchen is two thousand pounds",                         # 9
]
paths = make_notes(N, texts)
before = note_hashes(N)
sorting.sort_inbox(StandIn({
    "insurance": reading(home="NOW", kinds=["task"], intention="act"),
    "Invoices": reading(home="NOW", kinds=["task"], intention="act", organisations=["Lumen Bakery"]),
    "garage": reading(home="KNOWLEDGE", kinds=["knowledge"], people=["Dave"]),
    "Decided": reading(home="KNOWLEDGE", kinds=["decision"], memory="decision"),
    "prefer tea": reading(home="AREAS", kinds=["knowledge"], memory="preference"),
    "prefer coffee": reading(home="AREAS", kinds=["knowledge"], memory="preference"),
    "Ran 5k": reading(home="JOURNAL", kinds=["experience"], themes=["fitness"]),
    "Trip notes": reading(home="KNOWLEDGE", kinds=["knowledge"]),
    "birthday": reading(home="AREAS", kinds=["knowledge"], people=["Mum"]),
    "Budget": reading(home="AREAS", kinds=["knowledge"]),
}), {}, notes_dir=N)
loops.refresh_all(N)
ids = [rec_for(N, p)["id"] for p in paths]
def top(q, k=5, **kw): return [n["id"] for _, n, _ in retrieval.rank(q, N, k, **kw)]
def why(q, i):
    for _, n, r in retrieval.rank(q, N, 8):
        if n["id"] == ids[i]:
            return r
    return None

print("Test 1: the basics")
check("small talk finds nothing", retrieval.rank("good morning, how are you?", N) == [])
check("a title named in the question comes first", top("what about van insurance renewal")[0] == ids[0]
      and "title" in why("what about van insurance renewal", 0))
check("a whole title quoted in the question is 'named in your question'",
      "named in your question" in (why("remind me: Mum's birthday is on the 3rd of May", 8) or []))
check("word forms: 'invoice' finds 'Invoices', 'pay' finds 'paying'", top("which invoice do I need to pay")[0] == ids[1])
check("the whole note is searched, not just its opening lines", top("when does my passport expire")[0] == ids[7]
      and "in the note" in why("when does my passport expire", 7))
check("similar words: 'car' finds the van notes", ids[0] in top("anything about my car") or ids[3] in top("anything about my car"))
check("similar words: 'mother' finds Mum's birthday", top("when is my mother's birthday")[0] == ids[8])
check("similar words: 'money' finds the budget", ids[9] in top("how much money for the kitchen"))
check("names and themes Jarvis read: 'fitness' finds the 5k run", top("how's my fitness going")[:1] == [ids[6]]
      and "names and themes" in why("how's my fitness going", 6))

print("Test 2: related notes, category, decisions")
sc = rec_for(N, paths[2]); sc.setdefault("links", []).append({"kind": "related", "target_id": ids[0], "target_path": sc["path"],
                                                               "by": "you", "confirmed": True, "reason": "test"})
records.save(sc, N)
r = why("Mill Lane garage service", 0)
check("a note linked to a strong match comes along, and says why", r is not None and any("linked to" in x for x in r))
loops.create_project("Van stuff", [], notes_dir=N, kind="category")
cat = [s for s in records.load_all(N)[0].values() if (s.get("project_meta") or {}).get("kind") == "category"][0]
for i in (0, 3):
    s = rec_for(N, paths[i]); s["filed_under"] = cat["id"]; records.save(s, N)
r = why("van insurance renewal date", 3)
check("the same category comes along too", r is not None and any("same category" in x for x in r))
check("a 'why did I decide' question puts the decision first", top("why did I decide to keep the van")[0] == ids[3]
      and "a decision" in why("why did I decide to keep the van", 3))

print("Test 3: current before historical, and the other way round when asked")
knowledge.set_meta(ids[5], N, supersedes=ids[4])         # coffee replaced tea
q_now = "what do I drink in the mornings"
check("the current preference ranks above the replaced one", top(q_now).index(ids[5]) < top(q_now).index(ids[4])
      if ids[4] in top(q_now) else top(q_now)[0] == ids[5])
check("the replaced one says it's superseded", "superseded" in (why(q_now, 4) or ["superseded"]))
check("asking about the past turns it round", top("what did I previously drink in the mornings")[0] == ids[4])
g = retrieval.relevant_graph_nodes("what did I previously drink in the mornings",
                                   [{"id": k, "label": texts[k][:30], "excerpt": texts[k], "path": paths[k]} for k in range(len(paths))], N)
block = retrieval.notes_block(g)
check("the brain is told a replaced note is NOT current", "NOT current" in block and "superseded" in block)
check("...and which notes are only Jarvis's unconfirmed reading", "unconfirmed" in retrieval.notes_block(
    [dict(g[0], origin="ai_inferred", confidence="uncertain", current=True)]))
twins = make_notes(N, ["Plan the garden shed roof", "Plan the garden shed roof"])
knowledge.set_meta(rec_for(N, twins[1])["id"], N, importance=5)
check("importance lifts a note (same words, the more important one first)", top("garden shed roof")[0] == rec_for(N, twins[1])["id"])

print("Test 4: 'what do I currently prefer?' / 'what did I previously think?'")
a = retrieval.answer_preferences("What do I currently prefer in the mornings?", N)
check("current: coffee, not tea", a and "coffee" in a["spoken"].lower() and "Earlier, since replaced" in a["spoken"] and a["spoken"].lower().index("coffee") < a["spoken"].lower().index("tea"))
a = retrieval.answer_preferences("what did I previously prefer about the mornings", N)
check("previously: tea, then what's current", a and a["spoken"].startswith("Previously") and "tea" in a["spoken"].lower() and "current now" in a["spoken"])
a = retrieval.answer_preferences("what do I prefer about holidays", N)
check("nothing recorded: says so and won't guess", a and "won't guess" in a["spoken"])
check("an unconfirmed reading is marked as mine", "(my reading, not confirmed)" in retrieval.answer_preferences("what are my preferences", N)["spoken"])
check("ordinary questions aren't taken as preference questions",
      all(retrieval.preference_question(q) is None for q in ("what do I need to do today", "prefer coffee", "what's coming up")))

print("Test 5: speed on 500 notes")
B = tempfile.mkdtemp(prefix="jarvis-retr-big-")
make_notes(B, [f"Note {i} about {'garden van budget dentist holiday'.split()[i % 5]} and more words {i}" for i in range(500)])
t = time.time(); retrieval.rank("dentist appointment", B); first = time.time() - t
t = time.time(); retrieval.rank("van insurance", B); again = time.time() - t
check(f"500 notes: first search {first:.2f}s, next {again:.2f}s (under 3s / 1.5s)", first < 3 and again < 1.5)

print("Test 6: over HTTP -- chat, the thinking lights, and /ask")
W = tempfile.mkdtemp(prefix="jarvis-retrhttp-")
real = {k: getattr(server, k) for k in ("NOTES_DIR", "GRAPH_DATA_PATH", "call_brain", "load_config")}
real_run = server.subprocess.run
server.NOTES_DIR, server.GRAPH_DATA_PATH = N, os.path.join(W, "viewer", "graph-data.js")
os.makedirs(os.path.join(W, "viewer"))
server.subprocess.run = lambda cmd, cwd=None, **kw: real_run(cmd, cwd=W, **kw)
server.rebuild_graph()
seen = {}
def brain(config, messages):
    seen["system"] = messages[0]["content"]
    return "Coffee these days, sir.", "stand-in/chat", None
server.call_brain = brain
server.load_config = lambda: {"openrouter_api_key": "sk-or-v1-" + "ab" * 32}
httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
PORT = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()
def get(path): return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=10).read())
def post(path, payload):
    return json.loads(urllib.request.urlopen(urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST"), timeout=20).read())
try:
    r = get("/retrieve?q=" + urllib.parse.quote("when does my passport expire"))
    check("/retrieve: the lights get the whole-note match, with reasons", r["ids"] and r["why"][str(r["ids"][0])])
    d = post("/chat", {"message": "what do I drink in the mornings these days"})
    check("/chat answers from the new retrieval", d.get("answer") and d["nodes"])
    d2 = post("/chat", {"message": "what did I previously drink in the mornings"})
    check("asking about before: the replaced note goes to the brain, marked NOT current",
          "NOT current" in seen.get("system", "") and "prefer tea" in seen.get("system", "").lower())
    check("the brain was told what NOT current means", "used to be true" in seen.get("system", ""))
    a = post("/ask", {"question": "What do I currently prefer in the mornings?"})
    check("/ask answers preferences from your notes, no AI", a.get("intent") == "preferences" and "coffee" in a["spoken"].lower())
    check("/ask still answers the older questions", post("/ask", {"question": "what's coming up"}).get("intent") == "coming_up")
    broke = retrieval.relevant_graph_nodes
    retrieval.relevant_graph_nodes = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("test"))
    d = post("/chat", {"message": "when does my passport expire"})
    check("if the new search ever fails, chat falls back to the old one and still answers", d.get("answer"))
    retrieval.relevant_graph_nodes = broke
finally:
    httpd.shutdown()
    for k, v in real.items():
        setattr(server, k, v)
    server.subprocess.run = real_run

check("your notes unchanged by a byte (throughout)", all(note_hashes(N)[k] == v for k, v in before.items()))
for d in (N, B, W):
    shutil.rmtree(d, ignore_errors=True)
print(f"\n{PASS} passed, {FAIL} failed")
