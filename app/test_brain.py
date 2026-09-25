"""
4.7.0 (addendum R1): Jarvis stays Jarvis whichever model answers --
one personality, a session state Jarvis keeps itself, focused task packets,
and private details hidden before anything is sent. Stand-in models,
throwaway folders; your real notes are never touched. Run: python3 test_brain.py
"""
import datetime, json, os, shutil, tempfile, threading, urllib.request
from http.server import ThreadingHTTPServer
import brain, knowledge, records, server, sorting
from testkit import StandIn, reading, make_notes, note_hashes, rec_for

PASS = FAIL = 0


def check(label, ok):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
    print(f"  [{'OK' if ok else 'FAIL'}]{'   ' if ok else ' '}{label}")


print("Test 1: one personality, whoever answers")
check("the persona moved to brain.py word for word (server uses the same text)", server.SYSTEM_PROMPT is brain.PERSONALITY
      and brain.PERSONALITY.startswith("You are a dry, impeccably polite British butler"))
s = brain.new_state()
m1, _ = brain.build_packet("hello", s, "", [])
m2, _ = brain.build_packet("what's due?", s, "[Note 1] Rent", [])
check("every packet starts with the same core rules and personality",
      m1[0]["content"].split("TASK TYPE")[0] == m2[0]["content"].split("TASK TYPE")[0])
check("core rules: never claim to have done something; the user decides",
      "Never claim you did something" in m1[0]["content"] and "The user decides" in m1[0]["content"])
for sec in ("TASK TYPE", "CURRENT SESSION STATE", "RELEVANT MEMORY", "AVAILABLE TOOLS", "CONSTRAINTS", "REQUIRED OUTPUT"):
    check(f"packet has {sec}", sec in m2[0]["content"])
check("no notes -> says so rather than an empty section", "(no notes match this message)" in m1[0]["content"])

print("Test 2: privacy -- private details never reach a model")
t, n = brain.redact("wifi password: CorrectHorse9\ncard 4111 1111 1111 1111 exp 12/29\nkey sk-or-v1-" + "ab" * 20
                    + "\nIBAN GB29 NWBK 6016 1331 9268 19\nphone 07700 900123, order 1234567890123")
check("password hidden", "CorrectHorse9" not in t and "password: [hidden]" in t)
check("card number (Luhn-valid) hidden", "4111" not in t)
check("API key hidden", "sk-or-v1" not in t)
check("IBAN hidden", "NWBK" not in t)
check("ordinary numbers kept (phone, an order number that isn't a card)", "07700 900123" in t and "1234567890123" in t)
check("counted", n == 4)
msgs, hidden = brain.build_packet("my pin: 4321, what's my wifi?", s, "[Note 2] Wifi\npassword: CorrectHorse9", [])
check("hidden in the notes AND the question, and the model is told", "CorrectHorse9" not in json.dumps(msgs)
      and "4321" not in json.dumps(msgs) and hidden == 2 and "were hidden" in msgs[0]["content"])

print("Test 3: the session state Jarvis keeps")
N = tempfile.mkdtemp(prefix="jarvis-brain-")
st = brain.current(N)
check("starts as a new conversation", st["turns"] == 0 and "start of a new conversation" in brain.state_summary(st))
notes = [{"id": 3, "label": "Decided to keep the van", "type": "decision", "current": True},
         {"id": 4, "label": "Van insurance renewal", "type": "task", "current": True},
         {"id": 5, "label": "I prefer tea", "type": "preference", "current": False}]
brain.before_turn(st, "Should I sell the van before the Worcester trip with Zack?", notes)
check("topic, objective and entities kept", "van" in st["topic"] and "Worcester" in " ".join(st["entities"])
      and "Zack" in " ".join(st["entities"]) and st["objective"].startswith("Should I sell"))
check("the notes, decisions and tasks in play", st["decisions"] == ["Decided to keep the van"]
      and st["tasks"] == ["Van insurance renewal"] and len(st["notes"]) == 3)
brain.after_turn(st, "You decided to keep it for cheap repairs, sir. Shall I show you that decision?", "model/a", None, N)
check("Jarvis's offer is remembered, so 'yes' can be understood", st["pending_offer"] and "show you that decision" in st["pending_offer"])
check("the model that answered is recorded", st["current_model"] == "model/a" and st["turns"] == 1)
check("kept on disk (survives a restart)", brain.load(N)["pending_offer"] == st["pending_offer"])
summary = brain.state_summary(st)
check("summary: no-longer-current notes are marked", "I prefer tea (no longer current)" in summary)
check("summary: the reply may be answering the offer", "may be answering it" in summary)
brain.after_turn(st, "Very good.", "model/b", "Switched to model/b (model/a was busy).", N)
check("a model change is recorded with its reason", st["current_model"] == "model/b" and st["previous_models"] == ["model/a"]
      and "busy" in st["switch_reason"])
later = datetime.datetime.now().astimezone() + datetime.timedelta(minutes=brain.NEW_SESSION_AFTER_MIN + 5)
st2 = brain.current(N, now=later)
check("after a long quiet spell: a new conversation that remembers the last topic",
      st2["turns"] == 0 and st2["id"] != st["id"] and "van" in (st2["previous_topic"] or []))
check("thanks/ok doesn't overwrite what the user is after",
      brain.before_turn(brain.new_state(), "thanks, that's great", [])["objective"] is None)

print("Test 4 (addendum Scenario 1): model A starts, model B takes over, nothing repeated")
M = tempfile.mkdtemp(prefix="jarvis-brainhttp-")
paths = make_notes(M, ["Decided to keep the van another year because repairs are cheap",
                       "Van insurance renewal is due on the 1st of October", "wifi password: CorrectHorse9"])
before = note_hashes(M)
sorting.sort_inbox(StandIn({"Decided": reading(kinds=["decision"], memory="decision"),
                            "insurance": reading(home="NOW", kinds=["task"], intention="act"),
                            "wifi": reading()}), {}, notes_dir=M)
W = tempfile.mkdtemp(prefix="jarvis-brainw-")
real = {k: getattr(server, k) for k in ("NOTES_DIR", "GRAPH_DATA_PATH", "call_brain", "load_config")}
real_run = server.subprocess.run
server.NOTES_DIR, server.GRAPH_DATA_PATH = M, os.path.join(W, "viewer", "graph-data.js")
os.makedirs(os.path.join(W, "viewer"))
server.subprocess.run = lambda cmd, cwd=None, **kw: real_run(cmd, cwd=W, **kw)
server.rebuild_graph()
server.conversation_history.clear()
seen = []
answers = iter([("You decided to keep the van for cheap repairs, sir. Shall I list what's due on it?", "model/a", None),
                ("The insurance renewal on the 1st, sir.", "model/b", "Switched to model/b: model/a is unavailable."),
                ("Your wifi details are in your notes, sir.", "model/b", None)])
def brain_fn(config, messages):
    seen.append(messages)
    return next(answers)
server.call_brain = brain_fn
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
    post("/session/new", {})
    d1 = post("/chat", {"message": "Why did I decide to keep the van?"})
    check("turn 1 answered by model A", d1["model_used"] == "model/a")
    d2 = post("/chat", {"message": "yes please"})
    sys2 = seen[1][0]["content"]
    check("turn 2 is model B, and its brief carries the thread without the user repeating it",
          d2["model_used"] == "model/b" and "van" in sys2 and "Decided to keep the van" in sys2
          and "list what's due on it" in sys2 and "may be answering it" in sys2)
    s_now = get("/session")["session"]
    check("/session: model change recorded with its reason", s_now["current_model"] == "model/b"
          and s_now["previous_models"] == ["model/a"] and "unavailable" in s_now["switch_reason"])
    post("/chat", {"message": "what's my wifi password?"})
    check("the wifi password in a note never reached the model", "CorrectHorse9" not in json.dumps(seen[2]))
    a = post("/ask", {"question": "Jarvis, start a new conversation"})
    check("'start a new conversation' resets the thread", a["intent"] == "new_conversation"
          and get("/session")["session"]["turns"] == 0 and server.conversation_history == [])
finally:
    httpd.shutdown()
    for k, v in real.items():
        setattr(server, k, v)
    server.subprocess.run = real_run
check("notes unchanged by a byte", note_hashes(M) == before)
for d in (N, M, W):
    shutil.rmtree(d, ignore_errors=True)
print(f"\n{PASS} passed, {FAIL} failed")
