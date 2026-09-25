"""
4.8.0 (addendum R2 + learning): the model pool -- profiles from OpenRouter's
list, choosing by capability, one model per conversation, checking answers,
falling back by failure type, learning and benching per capability.
Stand-in OpenRouter, throwaway folders; your real notes and stats are never
touched. Run: python3 test_models.py
"""
import json, os, random, shutil, tempfile, threading, time, urllib.error, urllib.request
from http.server import ThreadingHTTPServer
import models, server, validate
from testkit import make_notes

PASS = FAIL = 0


def check(label, ok):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
    print(f"  [{'OK' if ok else 'FAIL'}]{'   ' if ok else ' '}{label}")


FREE = {"prompt": "0", "completion": "0"}
LIST = {"data": [
    {"id": "big/reasoner-70b:free", "name": "Big Reasoner 70B", "pricing": FREE, "context_length": 128000,
     "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
     "supported_parameters": ["reasoning", "tools", "response_format"]},
    {"id": "eye/see-it:free", "name": "See It", "pricing": FREE, "context_length": 32000,
     "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]}, "supported_parameters": []},
    {"id": "quick/mini-8b:free", "name": "Quick Mini 8B", "pricing": FREE, "context_length": 8000,
     "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}, "supported_parameters": []},
    {"id": "paid/fancy", "name": "Fancy", "pricing": {"prompt": "0.001", "completion": "0.002"}, "context_length": 200000,
     "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}, "supported_parameters": ["tools"]},
    {"id": "nvidia/nemotron-content-safety:free", "name": "Guard", "pricing": FREE, "context_length": 8000,
     "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}, "supported_parameters": []},
    {"id": "art/draw:free", "name": "Draw", "pricing": FREE, "context_length": 8000,
     "architecture": {"input_modalities": ["text"], "output_modalities": ["image"]}, "supported_parameters": []},
    {"id": "openrouter/free", "name": "Router", "pricing": FREE},
]}

print("Test 1: profiles from OpenRouter's list")
profs = {p["id"]: p for p in models.parse_list(LIST, server.BLOCKED_MODELS)}
check("only usable free text models (no paid, guard, image-output, or the router itself)",
      set(profs) == {"big/reasoner-70b:free", "eye/see-it:free", "quick/mini-8b:free"})
check("the 70B with reasoning+tools: GENERAL, REASONING, TOOL (not FAST)",
      set(profs["big/reasoner-70b:free"]["caps"]) == {"GENERAL", "REASONING", "TOOL"})
check("the image reader: VISION", "VISION" in profs["eye/see-it:free"]["caps"])
check("the 8B: FAST and GENERAL, not TOOL or VISION", set(profs["quick/mini-8b:free"]["caps"]) == {"GENERAL", "FAST"})
N = tempfile.mkdtemp(prefix="jarvis-models-")
calls = []
n, msg = models.refresh(N, fetch=lambda: (calls.append(1), LIST)[1], blocked=server.BLOCKED_MODELS)
check("refresh: saved 3 models", n == 3 and msg == "refreshed" and len(models.load_profiles(N)) == 3)
models.refresh(N, fetch=lambda: (calls.append(1), LIST)[1])
check("at most once a day unless asked", len(calls) == 1)
def boom(): raise OSError("offline")
n, msg = models.refresh(N, fetch=boom, force=True)
check("offline: keeps the last list and says so", n == 3 and "kept the last one" in msg)

print("Test 2: what a request needs")
for text, want in [("hello", "FAST"), ("thanks!", "FAST"), ("what's on for today", "GENERAL"),
                   ("should I sell the van or keep it?", "REASONING"),
                   ("compare the two quotes for the kitchen", "REASONING")]:
    check(f"'{text}' -> {want}", models.capability_for(text) == want)
check("an image -> VISION; a JSON plan -> TOOL",
      models.capability_for("x", has_image=True) == "VISION" and models.capability_for("x", needs_json=True) == "TOOL")

print("Test 3: choosing -- by capability, sticking with the conversation's model, fallback last")
r = random.Random(1)
check("REASONING: the reasoner first, openrouter/free last", models.candidates("REASONING", None, N, rng=r) ==
      ["big/reasoner-70b:free", models.FALLBACK])
check("VISION: only the image reader (+ fallback)", models.candidates("VISION", None, N, rng=r) == ["eye/see-it:free", models.FALLBACK])
g = models.candidates("GENERAL", "quick/mini-8b:free", N, rng=r)
check("session affinity: the conversation's model stays first while eligible", g[0] == "quick/mini-8b:free" and g[-1] == models.FALLBACK)
check("...but not if it can't do the job (no vision -> not preferred for VISION)",
      models.candidates("VISION", "quick/mini-8b:free", N, rng=r)[0] == "eye/see-it:free")

print("Test 4 (Scenario 4): bad at tools, fine for chat -- benched for TOOL only")
for _ in range(3):
    models.record("big/reasoner-70b:free", "TOOL", False, N, reason="malformed")
check("3 failures in a row at TOOL -> benched for TOOL", "big/reasoner-70b:free" not in models.candidates("TOOL", None, N, rng=r))
check("...still used for REASONING and GENERAL", models.candidates("REASONING", None, N, rng=r)[0] == "big/reasoner-70b:free"
      and "big/reasoner-70b:free" in models.candidates("GENERAL", None, N, rng=r))
later = time.time() + models.QUARANTINE_MIN * 60 + 5
check("...and back for TOOL once the rest is over", "big/reasoner-70b:free" in models.candidates("TOOL", None, N, now=later, rng=r))
models.record("quick/mini-8b:free", "GENERAL", False, N, reason="gone")
check("a 404 (model gone) benches it for everything", all(models.benched(models.load_stats(N), "quick/mini-8b:free", c) for c in models.CAPS))
models.record("eye/see-it:free", "VISION", False, N, reason="rate_limited")
st = models.load_stats(N)["eye/see-it:free"]["VISION"]["benched_until"]
check("a 429 rests it about 10 minutes", 9 * 60 < st - time.time() <= 10 * 60 + 1)
models.record("big/reasoner-70b:free", "GENERAL", False, N, reason="user_correction", weight=0.5)
s = models.load_stats(N)["big/reasoner-70b:free"]["GENERAL"]
check("a user correction is a weak signal: half a failure, no bench", s["fail"] == 0.5 and s["row"] == 0 and s["benched_until"] == 0)
M = tempfile.mkdtemp(prefix="jarvis-models-learn-")
models.refresh(M, fetch=lambda: LIST)
for _ in range(6):
    models.record("quick/mini-8b:free", "GENERAL", True, M, latency_ms=900)
    models.record("big/reasoner-70b:free", "GENERAL", False, M, reason="stale_as_current")
    models.record("big/reasoner-70b:free", "GENERAL", True, M, latency_ms=900)
check("learning: the model that's done better here comes first", models.candidates("GENERAL", None, M, rng=r)[0] == "quick/mini-8b:free")
check("summary for Settings", models.summary(M)["count"] == 3 and models.summary(M)["models"][0]["ok"] >= 6)

print("Test 5 (Scenario 3 and friends): checking answers without another AI")
notes = [{"label": "I prefer coffee in the mornings", "excerpt": "These days coffee", "current": True},
         {"label": "I prefer tea in the mornings", "excerpt": "tea with milk", "current": False}]
ok = lambda a, **k: validate.check_chat(a, "what do I drink?", notes, ["I prefer coffee in the mornings", "I prefer tea in the mornings"], **k)
check("a good answer passes", ok("Coffee these days, sir.")[0])
check("presenting the replaced note as current is caught", ok("You prefer tea with milk, sir.")[1] == "stale_as_current")
check("...but mentioning it as the past is fine", ok("Coffee now, sir; you used to prefer tea with milk.")[0])
check("claiming an action is caught", ok("I've added that to your notes, sir.")[1] == "claimed_action")
check("...but offering one isn't", ok("Coffee, sir. Shall I add a reminder? If you like, I've saved nothing yet.")[0])
check("naming a note that doesn't exist is caught", ok("Your note called 'Breakfast rules' says coffee.")[1] == "invented_note")
check("...naming a real one is fine", ok("Your note called 'I prefer coffee in the mornings' says so.")[0])
check("empty and safety verdicts are hard failures", ok("")[1] == "empty"
      and ok("The user is safe.", unusable=lambda a: server.unusable_reply(a, "x"))[1] == "safety_verdict")
check("a long canned reply that ignores the question is caught",
      ok("As an AI language model developed to be helpful, harmless and honest, I strive to provide balanced "
         "perspectives on a wide range of subjects including history, science, literature and the arts. " * 2)[1] == "ignored_request")
check("JSON check for plans/sorting", validate.check_json('```json\n{"notes": []}\n```')[0] and not validate.check_json("Sorry!")[0])
check("'no, that's wrong' is recognised as a correction; 'no problem' isn't",
      validate.is_correction("No, that's wrong, I told you coffee") and not validate.is_correction("nothing else, thanks"))

print("Test 6: the router inside Jarvis (stand-in OpenRouter)")
R = tempfile.mkdtemp(prefix="jarvis-router-")
models.refresh(R, fetch=lambda: LIST)
real = {k: getattr(server, k) for k in ("NOTES_DIR", "_call_openrouter_once", "runtime_override_model")}
server.NOTES_DIR, server.runtime_override_model = R, None
script, asked = [], []
def fake(api_key, model, messages):
    asked.append(model)
    step = script.pop(0)
    if isinstance(step, int):
        raise urllib.error.HTTPError("u", step, "x", {}, None)
    return step if isinstance(step, tuple) else (step, model)
server._call_openrouter_once = fake
cfg = {"openrouter_api_key": "k"}
try:
    script[:] = ["Morning, sir."]
    a, used, note = server.call_brain(cfg, [], capability="REASONING")
    check("REASONING goes to the reasoner, no switch message", used == "big/reasoner-70b:free" and note is None)
    asked.clear(); script[:] = [404, "Hello, sir."]
    a, used, _ = server.call_brain(cfg, [], capability="REASONING", prefer="big/reasoner-70b:free")
    check("model gone (404): the next one answers, silently", a == "Hello, sir." and asked[0] == "big/reasoner-70b:free" and len(asked) == 2)
    check("...and the gone one is benched", models.benched(models.load_stats(R), "big/reasoner-70b:free", "GENERAL"))
    shutil.rmtree(R); R = tempfile.mkdtemp(prefix="jarvis-router2-"); server.NOTES_DIR = R; models.refresh(R, fetch=lambda: LIST)
    asked.clear(); script[:] = ["I've saved that for you, sir.", "Coffee, sir."]
    a, used, _ = server.call_brain(cfg, [], capability="GENERAL", check=lambda x: ok(x))
    check("an answer that claims an action is set aside; another model's is used", a == "Coffee, sir." and len(asked) == 2)
    asked.clear(); script[:] = ["I've saved that, sir.", "I've added it, sir."]
    a, used, _ = server.call_brain(cfg, [], capability="GENERAL", check=lambda x: ok(x))
    check("two soft misses: the first answer is used rather than looping", a == "I've saved that, sir." and len(asked) == 2)
    asked.clear(); script[:] = ["Sure! Here you go: nothing to parse", '{"notes": []}']
    a, used, _ = server.tool_brain(cfg, [])
    check("Scenario 2: a malformed plan is rejected and retried", a == '{"notes": []}' and len(asked) == 2)
    asked.clear(); script[:] = [("x", "eye/see-it:free")]
    server.call_brain(cfg, [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:"}}]}])
    check("a photo goes to the image reader automatically", asked[0] == "eye/see-it:free")
    asked.clear(); script[:] = ["pinned answer"]
    a, used, _ = server.call_brain({"openrouter_api_key": "k", "model_chain": ["my/pinned-model"]}, [])
    check("a model_chain you pinned yourself is still obeyed", asked == ["my/pinned-model"])
    E = tempfile.mkdtemp(prefix="jarvis-router-empty-"); server.NOTES_DIR = E
    asked.clear(); script[:] = ["Fine, sir."]
    server.call_brain(cfg, [])
    check("no model list yet: openrouter/free, as before", asked == [models.FALLBACK])
    asked.clear(); script[:] = [429, 429, 429, 429]
    try:
        server.call_brain(cfg, []); raised = False
    except RuntimeError as e:
        raised = "429" in str(e)
    check("if every try fails it says so plainly (after 4 tries)", raised and len(asked) == 4)
finally:
    for k, v in real.items():
        setattr(server, k, v)

print("Test 7: over HTTP -- one model per conversation")
W = tempfile.mkdtemp(prefix="jarvis-models-http-")
H = tempfile.mkdtemp(prefix="jarvis-models-notes-")
make_notes(H, ["Van insurance renewal is due on the 1st of October"])
models.refresh(H, fetch=lambda: LIST)
real = {k: getattr(server, k) for k in ("NOTES_DIR", "GRAPH_DATA_PATH", "load_config", "_call_openrouter_once", "runtime_override_model")}
real_run = server.subprocess.run
server.NOTES_DIR, server.GRAPH_DATA_PATH, server.runtime_override_model = H, os.path.join(W, "viewer", "graph-data.js"), None
os.makedirs(os.path.join(W, "viewer"))
server.subprocess.run = lambda cmd, cwd=None, **kw: real_run(cmd, cwd=W, **kw)
server.rebuild_graph()
server.load_config = lambda: {"openrouter_api_key": "sk-or-v1-" + "ab" * 32}
asked.clear()
server._call_openrouter_once = lambda k, m, msgs: (asked.append(m), (f"On it, sir ({len(asked)}).", m))[1]
httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
PORT = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()
def get(path): return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=10).read())
def post(path, payload):
    return json.loads(urllib.request.urlopen(urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST"), timeout=20).read())
try:
    post("/session/new", {})
    first = post("/chat", {"message": "when is the van insurance renewal due?"})["model_used"]
    seconds = [post("/chat", {"message": m})["model_used"] for m in ("and what does it cover?", "who is it with?", "is it expensive?")]
    check("Scenario 7: the same model answers the whole conversation", first != models.FALLBACK and all(s == first for s in seconds))
    post("/chat", {"message": "No, that's wrong, I told you it's with Aviva"})
    s = models.load_stats(H).get(first, {})
    check("a correction is recorded against that model as a weak signal", any(v.get("fail") == 0.5 for v in s.values()))
    m = get("/models")
    check("/models: the pool, with what each is good for", m["count"] == 3 and all("caps" in x for x in m["models"]))
finally:
    httpd.shutdown()
    for k, v in real.items():
        setattr(server, k, v)
    server.subprocess.run = real_run
for d in (N, M, R, E, W, H):
    shutil.rmtree(d, ignore_errors=True)
print(f"\n{PASS} passed, {FAIL} failed")
