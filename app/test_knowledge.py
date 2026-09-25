"""
4.5.0 (Jarvis 5.0 Phase 1): what Jarvis knows about each note -- type,
source and confidence, time state, importance, last used, related notes --
and the one activity trail. Stand-in model, throwaway folders; your real
notes are never touched. Run: python3 test_knowledge.py
"""
import datetime, json, os, shutil, tempfile, threading, urllib.request
from http.server import ThreadingHTTPServer
import actions, activity, knowledge, loops, records, server, sorting
from testkit import StandIn, reading, make_notes, note_hashes, rec_for

PASS = FAIL = 0


def check(label, ok):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
    print(f"  [{'OK' if ok else 'FAIL'}]{'   ' if ok else ' '}{label}")


print("Test 1: types read from Jarvis's sorting, as metadata on unchanged notes")
N = tempfile.mkdtemp(prefix="jarvis-know-")
texts = ["Decided to go with the blue van because it's cheaper to insure",
         "Need to ring Dave by Friday about the van", "Waiting on Dave to send the quote",
         "I promised Sam I'd help him move on Saturday", "I prefer tea to coffee these days",
         "I'd love to visit Japan one day", "The boiler pressure should sit at 1.5 bar",
         "Work was chaotic today", "Maybe I should buy a bike", "The bin goes out on Tuesdays"]
paths = make_notes(N, texts)
before = note_hashes(N)
brain = StandIn({
    "Decided": reading(home="KNOWLEDGE", kinds=["decision"], memory="decision"),
    "ring Dave": reading(home="NOW", kinds=["task"], intention="act", people=["Dave"],
                         dates=[{"text": "Friday", "kind": "deadline", "iso": "2026-09-25"}]),
    "Waiting on": reading(home="NOW", kinds=["waiting_for"], intention="act", people=["Dave"]),
    "promised Sam": reading(home="NOW", kinds=["commitment"], intention="act", people=["Sam"]),
    "prefer tea": reading(home="AREAS", kinds=["knowledge"], memory="preference", confidence=0.95),
    "Japan": reading(home="WONDER", kinds=["wonder"], memory="interest", places=["Japan"]),
    "boiler": reading(home="KNOWLEDGE", kinds=["knowledge"], memory="fact", confidence=0.6),
    "chaotic": reading(home="JOURNAL", kinds=["reflection"], memory="reflection", temporary=True),
    "bike": reading(home="SOMEDAY", kinds=["purchase"], intention="maybe", confidence=0.4),
    "bin goes": reading(home="AREAS", kinds=[], memory="fact"),
}, model="stand-in/model-a")
sorting.sort_inbox(brain, {}, notes_dir=N)
loops.refresh_all(N)
nodes = {n["title"][:12]: n for n in knowledge.all_nodes(N)}
def n_for(i): return knowledge.node(rec_for(N, paths[i]), None, N)
want = ["decision", "task", "waiting", "commitment", "preference", "idea", "reference", "observation", None, "information"]
for i, t in enumerate(want):
    if t:
        check(f"'{texts[i][:34]}' reads as {t}", n_for(i)["type"] == t)
check("the Japan rule holds: 'maybe buy a bike' is NOT a task", n_for(8)["type"] != "task")
check("every type is one of the spec's ten", all(n["type"] in knowledge.TYPES for n in knowledge.all_nodes(N)))
check("notes themselves unchanged by a byte", note_hashes(N) == before)

print("Test 2: source and confidence -- an AI reading never becomes a fact on its own")
n = n_for(4)
check("you typed it: origin user_stated", n["origin"] == "user_stated")
check("Jarvis's reading of it: ai_inferred, 'likely', needs confirmation",
      n["classification"]["origin"] == "ai_inferred" and n["classification"]["confidence"] == "likely" and n["needs_confirmation"])
check("the model that read it is kept", n["classification"]["model"] == "stand-in/model-a")
check("0.6 -> 'uncertain'", n_for(6)["classification"]["confidence"] == "uncertain")
sorting.decide_for_you(rec_for(N, paths[4])["id"], "confirm", notes_dir=N)
n = n_for(4)
check("after you confirm: certain, yours, no longer needs confirmation",
      n["classification"]["confidence"] == "certain" and n["classification"]["origin"] == "user_stated" and not n["needs_confirmation"])
fp = os.path.join(N, "reference", "manual.md"); os.makedirs(os.path.dirname(fp))
open(fp, "w").write("# Manual\n\nA file dropped in.\n"); records.sync(N)
check("a file you put in notes/: user_recorded", knowledge.node(rec_for(N, fp), None, N)["origin"] == "user_recorded")
check("unsorted: no reading yet, needs confirmation", knowledge.node(rec_for(N, fp), None, N)["classification"]["origin"] is None)

print("Test 3: you set the type; Jarvis's reading stays underneath")
rid = rec_for(N, paths[9])["id"]
spoken, tok = knowledge.set_meta(rid, N, type="reference")
check("set: reference, marked as yours", n_for(9)["type"] == "reference" and n_for(9)["type_set_by"] == "you" and "reference" in spoken)
check("set by you counts as certain", n_for(9)["classification"]["confidence"] == "certain")
knowledge.set_meta(rid, N, clear="type")
check("'use Jarvis's type' goes back to the reading", n_for(9)["type"] == "information" and n_for(9)["type_set_by"] == "jarvis")
for bad in ({"type": "spaceship"}, {"state": "exploded"}, {"importance": 9}, {"importance": "lots"}, {}):
    try:
        knowledge.set_meta(rid, N, **bad); ok = False
    except ValueError:
        ok = True
    check(f"refused: {bad}", ok)

print("Test 4: time -- current vs historical, and replacing an older note")
check("a preference starts current", n_for(4)["state"] == "current")
new = make_notes(N, ["These days I prefer coffee again"])[0]
sorting.sort_inbox(StandIn({"coffee again": reading(home="AREAS", kinds=["knowledge"], memory="preference")}), {}, notes_dir=N)
new_id, old_id = rec_for(N, new)["id"], rec_for(N, paths[4])["id"]
spoken, tok = knowledge.set_meta(new_id, N, supersedes=old_id)
check("the old preference is kept, marked superseded", n_for(4)["state"] == "superseded" and n_for(4)["superseded_by"] == new_id)
check("the new one says what it replaces", old_id in knowledge.node(rec_for(N, new), None, N)["supersedes"])
check("superseded isn't current (for retrieval)", not knowledge.is_current(n_for(4)))
actions.undo(tok, N)
check("Undo: the old preference is current again", n_for(4)["state"] == "current" and not n_for(4)["superseded_by"])
dec2 = make_notes(N, ["Decided to sell the blue van after all"])[0]
sorting.sort_inbox(StandIn({"sell the blue": reading(home="KNOWLEDGE", kinds=["decision"], memory="decision")}), {}, notes_dir=N)
knowledge.set_meta(rec_for(N, dec2)["id"], N, supersedes=rec_for(N, paths[0])["id"])
check("a replaced decision reads 'reversed', and is kept", n_for(0)["state"] == "reversed" and os.path.exists(paths[0]))
check("the new decision is active", knowledge.node(rec_for(N, dec2), None, N)["state"] == "active")
knowledge.set_meta(rec_for(N, paths[7])["id"], N, state="historical")
check("you can mark something historical", n_for(7)["state"] == "historical" and n_for(7)["state_set_by"] == "you")
old_temp = rec_for(N, paths[7]); old_temp["meta"].pop("state")
old_temp["interpretations"][-1]["at"] = "2026-06-01T09:00:00+01:00"; records.save(old_temp, N)
check("a 'for now' note from months ago reads historical by itself", n_for(7)["state"] == "historical" and n_for(7)["state_set_by"] == "jarvis")
check("a task's state is its loop's", n_for(1)["state"] == "open")
knowledge.set_meta(rec_for(N, paths[1])["id"], N, state="done")
check("marking a task done closes its loop", rec_for(N, paths[1])["loop"]["state"] == "done" and n_for(1)["state"] == "done")
loops.create_project("Van", [rec_for(N, paths[2])["id"]], notes_dir=N)
pn = [n for n in knowledge.all_nodes(N) if n["type"] == "project"][0]
check("a project reads active", pn["state"] == "active")
knowledge.set_meta(pn["id"], N, state="abandoned")
pn = [n for n in knowledge.all_nodes(N) if n["type"] == "project"][0]
check("a project can be abandoned (new), kept, not deleted", pn["state"] == "abandoned")
check("the waiting item shows its project's related tasks/decisions fields", "related_tasks" in n_for(2))

print("Test 5: importance and last used")
check("an open loop due soon is more important than a plain fact", n_for(2)["importance"] > n_for(9)["importance"])
knowledge.set_meta(rec_for(N, paths[9])["id"], N, importance=5)
check("you can set importance", n_for(9)["importance"] == 5 and n_for(9)["importance_set_by"] == "you")
rec_before = open(os.path.join(records.sidecar_dir(N), rec_for(N, paths[6])["id"] + ".json")).read()
knowledge.touch_paths([paths[6]], N)
check("using a note records when", n_for(6)["last_referenced"])
check("...without rewriting its record", open(os.path.join(records.sidecar_dir(N), rec_for(N, paths[6])["id"] + ".json")).read() == rec_before)

print("Test 6: the activity trail")
acts = activity.read(N, 500)
check("sorting is there as Jarvis's reading, with the model", any(a["origin"] == "ai_inferred" and (a.get("detail") or {}).get("model") for a in acts))
check("your changes are there as yours", any(a["event"] == "type set" and a["origin"] == "user" for a in acts))
check("replacing a note is there", any(a["event"] == "replaced by a newer note" for a in acts))
check("undo is there", any(a["event"] == "undone by you" for a in acts))
check("newest first", acts[0]["at"] >= acts[-1]["at"])
check("per note", all(a["id"] == old_id for a in activity.read(N, 50, old_id)))
# a record saved before 4.5.0: its old history is not re-logged as new
O = tempfile.mkdtemp(prefix="jarvis-know-old-")
op = make_notes(O, ["An old note"])[0]
sc = rec_for(O, op); sc.pop("logged_events", None)
records._atomic_write_json(os.path.join(records.sidecar_dir(O), sc["id"] + ".json"), sc)
os.remove(activity.path(O))
sc = rec_for(O, op); records._event(sc, "something new"); records.save(sc, O)
check("an old record's history isn't re-logged; only the new event", [a["event"] for a in activity.read(O)] == ["something new"])
os.remove(activity.path(O)); os.makedirs(activity.path(O))          # the trail can't be written at all
sc = rec_for(O, op); records._event(sc, "saved anyway"); records.save(sc, O)
check("a trail that can't be written never stops a save", rec_for(O, op)["history"][-1]["event"] == "saved anyway")

print("Test 7: over HTTP")
W = tempfile.mkdtemp(prefix="jarvis-knowhttp-")
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
    d = get("/node?path=" + urllib.parse.quote(paths[5]))
    check("/node: the node, its history and the type choices", d["ok"] and d["node"]["type"] == "idea"
          and d["node"]["activity"] and d["node"]["type_choices"] == list(knowledge.TYPES))
    r = post("/node/set", {"path": paths[5], "type": "reference"})
    check("/node/set: changed, with Undo", r["ok"] and r["undo"] and r["node"]["type"] == "reference")
    check("/actions/undo puts it back", post("/actions/undo", {"token": r["undo"]})["ok"] and n_for(5)["type"] == "idea")
    r = post("/node/set", {"path": paths[8], "supersedes_path": paths[9]})
    check("/node/set supersedes_path (the page's right-click flow)", r["ok"] and n_for(9)["superseded_by"])
    check("/node/set refuses a bad type", not post("/node/set", {"path": paths[5], "type": "nope"})["ok"])
    check("/node refuses a path outside notes", not get("/node?path=" + urllib.parse.quote("/etc/passwd"))["ok"])
    check("/nodes: every note", len(get("/nodes")["nodes"]) == len(knowledge.all_nodes(N)))
    check("/activity", get("/activity?limit=5")["ok"] and len(get("/activity?limit=5")["activity"]) == 5)
finally:
    httpd.shutdown()
    for k, v in real.items():
        setattr(server, k, v)
    server.subprocess.run = real_run

check("after everything: your notes unchanged by a byte (new notes aside)",
      all(note_hashes(N)[k] == v for k, v in before.items()))
for d in (N, O, W):
    shutil.rmtree(d, ignore_errors=True)
print(f"\n{PASS} passed, {FAIL} failed")
