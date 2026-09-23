"""
3.7.0: take, link and file notes in one sentence.
Throwaway folders only. Run: python3 test_organise.py
"""
import json, os, shutil, tempfile
import organise, records, reviews
from testkit import note_hashes

PASS = FAIL = 0
def check(label, ok):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
    print(f"  [{'OK' if ok else 'FAIL'}]{'   ' if ok else ' '}{label}")

N = tempfile.mkdtemp(prefix="jarvis-organise-")
def note(text, created):
    p = os.path.join(N, "captures", f"{abs(hash(text))}.md"); os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w", encoding="utf-8").write(f"# {text}\n\nCaptured.\n\n{text}\n")
    sc = records.create_for_capture(p, text, source="typed", notes_dir=N)
    sc["created"] = created; records.save(sc, N); return sc["id"]
A = note("Check OpenRouter's privacy settings before sending personal writing", "2026-09-22T10:00:00")
B = note("Work out a good solution for reading and processing images", "2026-09-23T09:00:00")
saved = []
def capture(t):
    rid = note(t, "2026-09-23T12:00:00"); saved.append(rid); return rid
recs = lambda: records.load_all(N)[0]
before = None

print("Test 1: the sentence from first real use")
r = organise.handle("I want that linked in with the two existing notes and then all 3 filed as Jarvis Maintenance/improvements",
                    N, previous_text="I need a new mic", capture=capture)
M = saved[0] if saved else None
check("handled, and says everything it did", r["handled"] and r["spoken"].startswith("Saved 'I need a new mic', linked ")
      and "filed all three under a new category, 'Jarvis Maintenance/improvements'" in r["spoken"])
R = recs()
P = next((sc for sc in R.values() if sc.get("project_meta")), None)
lk = lambda x, y: any(l["target_id"] == y and l["kind"] == "related" for l in R[x].get("links", []))
check("the unsaved chat line was saved as a note first", M and "I need a new mic" in open(records.abs_path(R[M]["path"], N)).read())
check("all three linked to each other", lk(M, A) and lk(M, B) and lk(A, B))
check("a category note was made with your name for it (3.8.0: filing makes a category)", P and reviews._title(P, N) == "Jarvis Maintenance/improvements")
check("all three filed under it, each linked to the category's star; a category doesn't move them to Projects",
      P["project_meta"].get("kind") == "category" and
      all(R[x]["filed_under"] == P["id"] and R[x]["home"] != "PROJECTS" and lk(x, P["id"]) for x in (M, A, B)))
check("history says who did it", all(any(h["event"] == "filed by you" for h in R[x]["history"]) for x in (M, A, B)))
before = note_hashes(N)

print("Test 2: other ways of saying it")
def say(t, prev=None):
    return organise.handle(t, N, previous_text=prev, capture=capture)
check("same project, any capitalisation, isn't made twice", say("file the image note under jarvis maintenance/improvements")["spoken"]
      == "Filed 'Work out a good solution for reading and processing images' under 'Jarvis Maintenance/improvements', sir."
      and sum(1 for sc in recs().values() if sc.get("project_meta")) == 1)
check("'put X in the Y folder'", say("put the openrouter note in the Budget folder")["spoken"]
      == "Filed 'Check OpenRouter's privacy settings before sending personal writing' under a new category, 'Budget', sir.")
check("'the mic one'", say("connect the openrouter note with the mic one")["handled"])
check("'link the other notes together'", say("can you link the other notes together")["ok"])
check("'that' = your last note when nothing unsaved", say("link that to images")["spoken"].startswith("Linked 'I need a new mic'"))
check("unknown note named -> says which", say("link that to the spaceship note")["spoken"].endswith("'the spaceship note', sir."))

print("Test 3: everyday chat is left alone")
for t in ["can you put it in simple terms", "I want to file my taxes as soon as possible", "add that to the list",
          "tie my shoes to the bike", "how do I connect my headphones", "move on"]:
    check(f"'{t}' -> not handled", not say(t)["handled"])
check("…and nothing was saved by those", len(saved) == 1)

print("Test 4: the AI only interprets, and is checked")
def brain(reply):
    return lambda config, msgs: (reply, "stand-in", None)
odd = "the openrouter thing and the pictures thing belong together in my Jarvis stuff project"
r = organise.handle(odd, N, call_brain=brain(json.dumps({"link": [2, 3], "file_notes": [2, 3], "file_under": "Jarvis stuff"})), config={})
check("an odd phrasing, read by the AI, is carried out", r["handled"] and "under a new project, 'Jarvis stuff'" in r["spoken"])
r = organise.handle(odd.replace("Jarvis stuff", "tech"), N,
                    call_brain=brain(json.dumps({"link": [], "file_notes": [1], "file_under": "Secret Plans"})), config={})
check("a project name you never said is refused", not any(reviews._title(sc, N) == "Secret Plans" for sc in recs().values()))
r = organise.handle("link the notes about the thing please", N,
                    call_brain=brain(json.dumps({"link": [98, 99], "file_notes": [], "file_under": ""})), config={})
check("note numbers that don't exist are refused", not r.get("ok") or "Linked" not in r.get("spoken", ""))
r = organise.handle("how do I connect my headphones", N, call_brain=brain(json.dumps({"link": [1, 2]})), config={})
check("chat about something else never reaches the AI interpreter", not r["handled"])
check("a guard-model style reply does nothing", not organise.handle("link the notes about stuff", N,
      call_brain=brain("The user is safe."), config={}).get("ok"))

print("Test 5: your notes")
check("no note's text was changed by linking or filing", all(v == note_hashes(N)[k] for k, v in before.items()))
shutil.rmtree(N, ignore_errors=True)
print(f"\n{PASS} passed, {FAIL} failed")
