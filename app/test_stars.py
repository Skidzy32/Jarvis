"""
3.8.0: the right-click star menu, categories, "that", and the model blocklist.
Throwaway folders only. Run: python3 test_stars.py
"""
import os, shutil, sys, tempfile
import actions, loops, organise, records, stars
from testkit import make_notes, note_hashes, rec_for

PASS = FAIL = 0
def check(label, ok):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
    print(f"  [{'OK' if ok else 'FAIL'}]{'   ' if ok else ' '}{label}")

N = tempfile.mkdtemp(prefix="jarvis-stars-")
paths = make_notes(N, ["I need a new mic", "Check OpenRouter privacy settings", "Work out image reading", "Dave birthday ideas"])
before = note_hashes(N)
txt = lambda p: open(p, encoding="utf-8").read()

print("Test 1: rename star")
line, tok = stars.rename(paths[0], "New microphone", N)
check("renamed, said so", line == "Renamed the star 'I need a new mic' to 'New microphone', sir." and tok)
check("title line changed, rest of the note kept", txt(paths[0]).startswith("# New microphone\n") and "I need a new mic" in txt(paths[0]))
sc = rec_for(N, paths[0])
check("previous version kept", any(os.listdir(os.path.join(N, ".jarvis", "versions", sc["id"]))))
check("record knows (no 'edited outside Jarvis' on next sync)", not records.sync(N)["edited"])
actions.undo(tok, N)
check("undo: exact bytes back, version copy removed", note_hashes(N) == before and not os.listdir(os.path.join(N, ".jarvis", "versions", sc["id"])))

print("Test 2: change star contents")
line, tok = stars.edit(paths[1], "# Check OpenRouter privacy settings\n\nDone: training off.\n", N)
check("saved", line.startswith("Saved your changes") and "Done: training off." in txt(paths[1]))
try:
    stars.edit(paths[1], "   ", N); ok = False
except ValueError: ok = True
check("an empty note is refused (use delete)", ok)
actions.undo(tok, N)
check("undo restores the text", note_hashes(N) == before)

print("Test 3: delete star")
line, tok = stars.delete(paths[3], N)
bin_dir = os.path.join(N, ".jarvis", "bin")
check("gone from the notes, kept in the bin", not os.path.exists(paths[3]) and len(os.listdir(bin_dir)) == 1)
recs = records.load_all(N)[0]
d = next(s for s in recs.values() if s.get("deleted"))
check("record says where it went", d["missing"] and d["deleted"]["bin"].startswith(".jarvis/bin/"))
actions.undo(tok, N)
check("undo: note back byte-for-byte, bin empty", note_hashes(N) == before and not os.listdir(bin_dir))
check("record no longer deleted", not rec_for(N, paths[3]).get("missing"))

print("Test 4: link by right-click")
line, tok = stars.link(paths[0], paths[2], N)
a, c = rec_for(N, paths[0]), rec_for(N, paths[2])
check("linked", line.startswith("Linked") and any(l["target_id"] == c["id"] for l in a["links"]))
try:
    stars.link(paths[0], paths[0], N); ok = False
except ValueError: ok = True
check("a star can't link to itself", ok)
actions.undo(tok, N)
check("undo unlinks", not rec_for(N, paths[0]).get("links"))

print("Test 5: categories")
line, tok = stars.categorise([paths[0], paths[1], paths[2]], "Jarvis Maintenance", N)
cats = loops.overview(N)["categories"]
check("a group put in a new category", line.startswith("Put 'I need a new mic', ") and "a new category, 'Jarvis Maintenance'" in line
      and cats and cats[0]["count"] == 3)
check("a category isn't a project", not loops.overview(N)["projects"])
check("the category is its own star, in notes/categories", os.path.isfile(os.path.join(N, "categories", "jarvis-maintenance.md")))
line2, _ = stars.categorise([paths[2]], "Research", N)
check("changing category moves it (one place at a time)", rec_for(N, paths[2])["filed_under"] != rec_for(N, paths[0])["filed_under"]
      and {c["title"]: c["count"] for c in loops.overview(N)["categories"]} == {"Jarvis Maintenance": 2, "Research": 1})
check("and its line to the old category's star is gone", not any(l.get("reason") == "filed together" and
      l["target_id"] == rec_for(N, paths[0])["filed_under"] for l in rec_for(N, paths[2])["links"]))
catpath = os.path.join(N, "categories", "jarvis-maintenance.md")
line3, _ = stars.rename(catpath, "Jarvis Upkeep", N)
check("renaming the category's star renames the category", line3.startswith("Renamed the category")
      and {c["title"] for c in loops.overview(N)["categories"]} == {"Jarvis Upkeep", "Research"})
check("filing under the new name finds it (no duplicate)", "a new" not in stars.categorise([paths[3]], "jarvis upkeep", N)[0])
line4, _ = stars.uncategorise([paths[3]], N)
check("take out of category", line4.startswith("Taken out") and not rec_for(N, paths[3]).get("filed_under"))
line5, tok5 = stars.delete(os.path.join(N, "categories", "jarvis-maintenance.md"), N)
check("deleting a category keeps its notes, just unfiled", not rec_for(N, paths[0]).get("filed_under") and os.path.exists(paths[0]))
actions.undo(tok5, N)
check("undo brings the category and its filing back", rec_for(N, paths[0]).get("filed_under") and
      os.path.exists(os.path.join(N, "categories", "jarvis-maintenance.md")))
check("voice: 'file X and Y as Z' makes a category", "a new category" in organise.handle(
      "file dave birthday and image reading as Personal stuff", N)["spoken"])
check("voice: say 'project' to get a project", "a new project" in organise.handle(
      "file dave birthday under the Party project", N)["spoken"])

print("Test 6: 'that' = the last thing discussed")
r = organise.handle("link that with the dave note", N, previous_text="some chat line", capture=lambda t: 1 / 0,
                    focus_id=rec_for(N, paths[1])["id"])
check("with a star just discussed, 'that' is that star (and nothing is captured)",
      r["ok"] and "Check OpenRouter privacy settings" in r["spoken"] and "Dave birthday ideas" in r["spoken"])
cat = actions.catalogue(N, focus_id=rec_for(N, paths[2])["id"])
check("the AI's list marks it [just discussed]", "[just discussed]" in actions.plan_messages("x", [], cat, [])[1]["content"])

print("Test 7: only real notes can be touched")
for bad in (os.path.join(N, ".jarvis", "records", "x.json"), "/etc/passwd", os.path.join(N, "..", "outside.md")):
    try:
        stars.rename(bad, "x", N); ok = False
    except ValueError: ok = True
    check(f"refused: {os.path.basename(bad)}", ok)

print("Test 7b: the galaxy draws categories as planets with notes in orbit")
import json, subprocess
G = tempfile.mkdtemp(); os.makedirs(os.path.join(G, "viewer"))
subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "build.py"), N], cwd=G, check=True, capture_output=True)
t = open(os.path.join(G, "viewer", "graph-data.js"), encoding="utf-8").read(); g = json.loads(t[t.index("{"):t.rindex("}") + 1])
planets = {n["id"] for n in g["nodes"] if n["group"] == "categories"}
check("categories are in the 'categories' group (drawn as planets)", len(planets) >= 2)
check("filed notes' lines are marked orbit", any(l.get("orbit") for l in g["links"]))
check("two categories aren't joined just by Jarvis's stock line", not any(l["source"] in planets and l["target"] in planets for l in g["links"]))
shutil.rmtree(G, ignore_errors=True)

print("Test 8: the model blocklist")
sys.argv = ["server.py"]
import server
check("nvidia/nemotron-3.5-content-safety:free is blocked", server.unusable_reply("A perfectly normal answer.", "nvidia/nemotron-3.5-content-safety:free"))
for m in ("meta-llama/llama-guard-4-12b:free", "google/shieldgemma-2:free", "some/safety-classifier:free", "x/content-safety-v2:free"):
    check(f"lookalike blocked: {m}", server.unusable_reply("fine", m))
check("an ordinary model isn't", not server.unusable_reply("Hello, sir.", "nvidia/nemotron-3-super:free"))

for d in (N,): shutil.rmtree(d, ignore_errors=True)
print(f"\n{PASS} passed, {FAIL} failed")
