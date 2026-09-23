"""
3.6.0: "link X and Y" / "unlink", and throwing away safety-verdict replies.
Throwaway folders only; your real notes and galaxy are never touched.
Run: python3 test_links.py
"""
import json, os, shutil, subprocess, sys, tempfile
import links, records, sorting
from testkit import StandIn, reading, make_notes, note_hashes, rec_for

PASS = FAIL = 0
def check(label, ok):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
    print(f"  [{'OK' if ok else 'FAIL'}]{'   ' if ok else ' '}{label}")

HERE = os.path.dirname(os.path.abspath(__file__))
N = tempfile.mkdtemp(prefix="jarvis-links-")
texts = ["Iceland trip in March", "Packing list", "Pros and cons of moving", "Call Dave about the van",
         "Dave birthday ideas", "Garden plan for spring", "Garden shed spring repairs", "Budget for the year"]
paths = make_notes(N, texts)
before = note_hashes(N)
T = {t: rec_for(N, p) for t, p in zip(texts, paths)}
U = lambda text: links.understand(text, N)
titles = lambda r: [n["title"] for n in r["notes"]] if r["ok"] else r["spoken"]

print("Test 1: working out which notes you mean")
check("exact titles", titles(U("Iceland trip in March and Packing list")) == ["Iceland trip in March", "Packing list"])
check("part of a title, any case", titles(U("iceland and packing")) == ["Iceland trip in March", "Packing list"])
check("'to' / 'with' work as joiners", titles(U("the iceland note to the packing list")) == ["Iceland trip in March", "Packing list"])
check("a title containing 'and' still works",
      titles(U("pros and cons of moving and budget")) == ["Pros and cons of moving", "Budget for the year"])
check("three at once", titles(U("iceland, packing list and budget")) == ["Iceland trip in March", "Packing list", "Budget for the year"])
check("quotes are ignored", titles(U('"Iceland trip in March" and "Packing list"')) == ["Iceland trip in March", "Packing list"])
r = U("garden and budget")
check("two equally good matches -> asks, with the options", not r["ok"] and set(r["options"]) == {"Garden plan for spring", "Garden shed spring repairs"}
      and "Which one?" in r["spoken"])
check("each option comes with a ready retry that resolves", all(U(t)["ok"] for t in r["retry"]))
r = U("iceland and spaceship")
check("unknown title -> says which, and how to name it", not r["ok"] and "'spaceship'" in r["spoken"])
check("only one note named -> asks for both", not U("iceland")["ok"])

print("Test 2: linking")
line = links.apply("link", U("iceland and packing")["notes"], N)
a, b = rec_for(N, paths[0]), rec_for(N, paths[1])
check("spoken", line == "Linked 'Iceland trip in March' and 'Packing list', sir.")
check("both records hold the link, by you, confirmed",
      any(l["target_id"] == b["id"] and l["by"] == "you" and l["confirmed"] for l in a["links"])
      and any(l["target_id"] == a["id"] and l["by"] == "you" for l in b["links"]))
check("history says so", a["history"][-1]["event"] == "linked by you")
check("again -> already linked, no duplicate", links.apply("link", U("iceland and packing")["notes"], N).endswith("already linked, sir.")
      and len([l for l in rec_for(N, paths[0])["links"] if l["target_id"] == b["id"]]) == 1)
check("three notes -> linked to each other", links.apply("link", U("dave birthday, van and budget")["notes"], N)
      .endswith("to each other, sir.") and len(rec_for(N, paths[7])["links"]) == 2)

print("Test 3: unlinking sticks")
line = links.apply("unlink", U("iceland and packing")["notes"], N)
a = rec_for(N, paths[0])
check("spoken", line.startswith("Unlinked 'Iceland trip in March' and 'Packing list'"))
check("link removed, pair remembered", not any(l["target_id"] == b["id"] for l in a.get("links", []))
      and b["id"] in a["unlinked"])
# sorting sees both mention Dave -> would suggest a link; unlink first, then sort
links.apply("unlink", U("call dave and dave birthday")["notes"], N)
sorting.sort_inbox(StandIn({"Dave": reading(people=["Dave"])}), {}, notes_dir=N)
d1, d2 = rec_for(N, paths[3]), rec_for(N, paths[4])
check("sorting doesn't suggest an unlinked pair again", not any(l["target_id"] == d2["id"] for l in d1.get("links", [])))
check("link again clears the unlink", links.apply("link", U("iceland and packing")["notes"], N).startswith("Linked")
      and b["id"] not in rec_for(N, paths[0]).get("unlinked", []))

print("Test 4: the galaxy")
G = tempfile.mkdtemp(prefix="jarvis-galaxy-"); os.makedirs(os.path.join(G, "viewer"))
def galaxy():
    subprocess.run([sys.executable, os.path.join(HERE, "build.py"), N], cwd=G, check=True, capture_output=True)
    t = open(os.path.join(G, "viewer", "graph-data.js"), encoding="utf-8").read()
    g = json.loads(t[t.index("{"):t.rindex("}") + 1])
    ids = {os.path.basename(n["path"]): n["id"] for n in g["nodes"]}
    pairs = {tuple(sorted((l["source"], l["target"]))): l for l in g["links"]}
    return ids, pairs
ids, pairs = galaxy()
pk = lambda x, y: tuple(sorted((ids[os.path.basename(paths[x])], ids[os.path.basename(paths[y])])))
check("your link shows in the galaxy, as yours (not 'suggested')", pk(0, 1) in pairs and pairs[pk(0, 1)].get("suggested") is False)
check("garden notes share words -> auto-linked before", pk(5, 6) in pairs)
links.apply("unlink", U("garden plan and garden shed")["notes"], N)
ids, pairs = galaxy()
check("unlink beats the automatic word-match link", pk(5, 6) not in pairs)
check("unlinked Dave pair stays apart in the galaxy", pk(3, 4) not in pairs)

print("Test 5: safety-verdict replies are thrown away")
sys.argv = ["server.py"]
import server
seq = []
def fake(api_key, model, messages):
    return seq.pop(0)
server._call_openrouter_once = fake
server.runtime_override_model = None
cfg = {"openrouter_api_key": "k", "model_chain": ["openrouter/free", "openrouter/free", "openrouter/free"]}
seq[:] = [("The user is safe.", "some/model:free"), ("unsafe\nS2", "x/y:free"), ("Hello, sir.", "good/model:free")]
ans, used, _ = server.call_brain(cfg, [])
check("'the user is safe.' and 'unsafe S2' skipped; the real answer returned", ans == "Hello, sir." and used == "good/model:free")
seq[:] = [("Anything at all", "meta-llama/llama-guard-4-12b:free"), ("Sure.", "good/model:free")]
check("a guard model's reply is skipped whatever it says", server.call_brain(cfg, [])[0] == "Sure.")
seq[:] = [("Safe travels, sir! Iceland in March is lovely.", "good/model:free")]
check("a real answer that starts with 'Safe' is kept", server.call_brain(cfg, [])[0].startswith("Safe travels"))
seq[:] = [("The user is safe.", "a:free")] * 4
try:
    server.call_brain(cfg, []); ok = False
except RuntimeError as e:
    ok = "safety verdict" in str(e)
check("if every try is a verdict, it says so plainly", ok)
check("single-model chain still gets 4 tries", (seq.__setitem__(slice(None), [("safe", "a:free")] * 3 + [("Yes.", "b:free")]),
      server.call_brain({"openrouter_api_key": "k", "model_chain": ["openrouter/free"]}, [])[0])[1] == "Yes.")

print("Test 6: notes untouched")
check("every note byte-for-byte unchanged", note_hashes(N) == before)
for d in (N, G): shutil.rmtree(d, ignore_errors=True)
print(f"\n{PASS} passed, {FAIL} failed")
