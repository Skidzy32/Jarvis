"""
3.7.0: requests in your own words -> a checked plan -> done, with undo.
A scripted stand-in plays the model; throwaway folders only.
Run: python3 test_actions.py
"""
import json, os, shutil, subprocess, sys, tempfile
import actions, links, loops, records
from testkit import make_notes, note_hashes, rec_for

PASS = FAIL = 0
def check(label, ok):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
    print(f"  [{'OK' if ok else 'FAIL'}]{'   ' if ok else ' '}{label}")

HERE = os.path.dirname(os.path.abspath(__file__))
N = tempfile.mkdtemp(prefix="jarvis-actions-")
paths = make_notes(N, ["Check OpenRouter's privacy settings before sending personal writing",
                       "Work out a good solution for reading and processing images"])
before = note_hashes(N)

def remember_fn(text):
    p = os.path.join(N, "captures", "i-need-a-new-mic.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write(f"# {text}\n\n{text}\n")
    sc = records.create_for_capture(p, text, source="typed", notes_dir=N)
    return sc["id"], sc["path"]

class Brain:
    def __init__(self, reply): self.reply, self.seen = reply, None
    def __call__(self, config, messages):
        self.seen = messages
        if isinstance(self.reply, Exception): raise self.reply
        return (self.reply if isinstance(self.reply, str) else json.dumps(self.reply)), "stand-in/model", None

HIST = [{"who": "user", "text": "I need a new mic"}, {"who": "assistant", "text": "Noted, sir."}]
REQ = "link that in with the two existing notes and file all 3 as Jarvis Maintainance/improvments"

print("Test 1: your exact request")
b = Brain({"actions": [{"do": "remember", "text": "I need a new mic"},
                       {"do": "link", "notes": [0, 1, 2]},
                       {"do": "file", "notes": [0, 1, 2], "collection": "Jarvis Maintenance/Improvements"}]})
out = actions.handle(REQ, HIST, b, {}, N, remember_fn)
check("done", out["ok"] and out["undo"])
user_msg = b.seen[1]["content"]
check("the model saw the conversation, the request and numbered notes",
      "user: I need a new mic" in user_msg and REQ in user_msg and '1. "' in user_msg and '2. "' in user_msg)
recs = records.load_all(N)[0]
mic = next(s for s in recs.values() if "mic" in s["path"])
col = next(s for s in recs.values() if s.get("project_meta"))
a, c = rec_for(N, paths[0]), rec_for(N, paths[1])
check("the mic note was saved from what you said", mic and "I need a new mic" in open(records.abs_path(mic["path"], N)).read())
rel = lambda x: {l["target_id"] for l in x.get("links", []) if l["kind"] == "related"}
check("all three linked to each other", {a["id"], c["id"]} <= rel(mic) and {mic["id"], c["id"]} <= rel(a) and {mic["id"], a["id"]} <= rel(c))
check("a collection 'Jarvis Maintenance/Improvements' made (spelled properly)",
      open(records.abs_path(col["path"], N)).read().startswith("# Jarvis Maintenance/Improvements"))
check("all three filed in it, and linked to its star", all(x.get("filed_under") == col["id"] and col["id"] in rel(x) for x in (mic, a, c)))
check("Jarvis says what it did, in its own words", out["spoken"].startswith("Saved 'I need a new mic'. Linked ")
      and "Filed 'I need a new mic', " in out["spoken"] and "under a new category, 'Jarvis Maintenance/Improvements', sir." in out["spoken"])
check("listed as a category with 3 filed (not as a project)", next(p for p in loops.overview(N)["categories"] if p["id"] == col["id"])["count"] == 3
      and not any(p["id"] == col["id"] for p in loops.overview(N)["projects"]))
G = tempfile.mkdtemp(); os.makedirs(os.path.join(G, "viewer"))
subprocess.run([sys.executable, os.path.join(HERE, "build.py"), N], cwd=G, check=True, capture_output=True)
t = open(os.path.join(G, "viewer", "graph-data.js"), encoding="utf-8").read(); g = json.loads(t[t.index("{"):t.rindex("}") + 1])
star = {os.path.basename(n["path"]): n["id"] for n in g["nodes"]}
pairs = {tuple(sorted((l["source"], l["target"]))) for l in g["links"]}
cid = star[os.path.basename(col["path"])]
check("galaxy: the collection's star joins all three", all(tuple(sorted((cid, star[os.path.basename(p)]))) in pairs
      for p in [mic["path"], a["path"], c["path"]]))
check("your original notes untouched", all(note_hashes(N)[k] == v for k, v in before.items()))

print("Test 2: undo")
line = actions.undo(out["undo"], N)
recs = records.load_all(N)[0]
check("undone", line.startswith("Undone"))
check("the collection and the saved-from-chat note are gone", not any(s.get("project_meta") for s in recs.values())
      and not any("mic" in s["path"] for s in recs.values()) and not os.path.exists(records.abs_path(col["path"], N)))
check("the two notes' records are back as they were", not rel(rec_for(N, paths[0])) and not rec_for(N, paths[0]).get("filed_under"))
try:
    actions.undo(out["undo"], N); ok = False
except ValueError:
    ok = True
check("second undo says so", ok)

print("Test 3: the checks on the model's plan")
H2 = [{"who": "user", "text": "hello"}]
out = actions.handle("file 1 and 2 as Jarvis stuff", H2, Brain({"actions": [{"do": "file", "notes": [1, 2], "collection": "Secret Plans"}]}), {}, N, remember_fn)
check("a collection name you didn't say is refused", not out["ok"] and "you didn't say" in out["spoken"])
out = actions.handle("link those", H2, Brain({"actions": [{"do": "link", "notes": [1, 7]}]}), {}, N, remember_fn)
check("an invented note number is refused (and one note can't be linked)", not out["ok"] and "don't exist" in out["spoken"])
out = actions.handle("remember that and link it", H2, Brain({"actions": [{"do": "remember", "text": "buy a boat"}]}), {}, N, remember_fn)
check("a 'remember' of words you never said is refused", not out["ok"] and "you didn't say" in out["spoken"])
out = actions.handle("link the privacy one", H2, Brain({"actions": [], "question": "Link it to which note, sir: 'Work out…'?"}), {}, N)
check("the model's one question is passed on", not out["ok"] and out["spoken"].startswith("Link it to which note"))
out = actions.handle("what should I file my taxes under?", H2, Brain({"actions": [], "chat": True}), {}, N)
check("not about the notes -> handed back to normal chat", out.get("chat") is True)
out = actions.handle("link them", H2, Brain(RuntimeError("Every model in the chain failed")), {}, N)
check("brain down -> says so, suggests the direct phrase", not out["ok"] and "link mic and image reading" in out["spoken"])
out = actions.handle("link them", H2, Brain("Sure! I'd love to help link your notes."), {}, N)
check("a chatty non-JSON reply -> honest failure, nothing changed", not out["ok"] and not rel(rec_for(N, paths[0])))
col2 = loops.create_project("Jarvis Maintenance/Improvements", [], notes_dir=N)
num = lambda path: next(c["n"] for c in actions.catalogue(N) if c["id"] == rec_for(N, path)["id"])
out = actions.handle("put the image one in jarvis maintenance", H2,
                     Brain({"actions": [{"do": "file", "notes": [num(paths[0])], "collection": "Jarvis maintenance/improvements"}]}), {}, N)
check("an existing collection is reused, not duplicated", out["ok"] and "a new " not in out["spoken"]
      and sum(1 for s in records.load_all(N)[0].values() if s.get("project_meta")) == 1
      and rec_for(N, paths[0]).get("filed_under") == col2["id"])
out = actions.handle("move the privacy note to someday", H2, Brain({"actions": [{"do": "move", "notes": [num(paths[1])], "home": "SOMEDAY"}]}), {}, N)
check("move", out["ok"] and rec_for(N, paths[1])["home"] == "SOMEDAY" and "Moved" in out["spoken"])

print("Test 4: what counts as an action request")
for t in ("link that in with the two existing notes", "file all 3 as Jarvis maintenance", "put it with the mic note", "move that to someday"):
    check(f"hint: '{t}'", actions.ACTION_HINT_RE.search(t))
check("an ordinary question isn't", not actions.ACTION_HINT_RE.search("what's the weather like"))

print("Test 5: notes untouched")
check("original notes byte-for-byte unchanged", all(note_hashes(N)[k] == v for k, v in before.items()))
for d in (N, G): shutil.rmtree(d, ignore_errors=True)
print(f"\n{PASS} passed, {FAIL} failed")
