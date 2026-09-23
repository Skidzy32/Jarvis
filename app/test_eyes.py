"""
Tests for Prompt 13 (3.1.0): the eyes. The posture timing is the page's own
eyesStep() run in Node; the server routes run for real. The camera and the
MediaPipe models can't run here (no webcam, no internet) -- see the notes.
Run: python3 test_eyes.py
"""
import json, os, re, subprocess, threading, time, urllib.error, urllib.request
from http.server import ThreadingHTTPServer
import focus_session as fs
import server

PASS = FAIL = 0
# Leave the real focus ledger exactly as found (this test ends a real session).
import atexit
_LEDGER = open(fs.LEDGER_PATH, "rb").read() if os.path.exists(fs.LEDGER_PATH) else None
def _restore():
    if _LEDGER is None:
        if os.path.exists(fs.LEDGER_PATH):
            os.remove(fs.LEDGER_PATH)
    else:
        open(fs.LEDGER_PATH, "wb").write(_LEDGER)
atexit.register(_restore)
ROOT = os.path.dirname(os.path.abspath(__file__))


def check(label, ok):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
    print(f"  [{'OK' if ok else 'FAIL'}]{'   ' if ok else ' '}{label}")


html = open(os.path.join(ROOT, "viewer", "index.html"), encoding="utf-8").read()
eyes_block = html.split("// ---------- Prompt 13: the eyes ----------")[1].split("// ---------- 2.8.0: the overview")[0]
consts = re.search(r"const EYES = \{.*?\};", eyes_block, re.S).group(0)
step = re.search(r"function eyesStep\(st, obs, now\) \{.*?\n\}", eyes_block, re.S).group(0)

print("Test 1: posture timing (the page's own code, in Node)")
scenarios = r"""
function run(seq, st) { st = st || {cond:null, since:0, fired:false, cooldownUntil:0, quietUntil:0}; const out = [];
  for (const [t, obs] of seq) for (const e of eyesStep(st, obs, t)) out.push([t, e]); return out; }
const ok = {present:true, headDown:false, slouched:false}, phone = {present:true, headDown:true, slouched:false},
      slouch = {present:true, headDown:false, slouched:true}, gone = {present:false, headDown:false, slouched:false};
const ticks = (from, to, obs, step=100) => { const a=[]; for (let t=from; t<=to; t+=step) a.push([t, obs]); return a; };
const R = {};
R.short = run([...ticks(0, 500, ok), ...ticks(600, 1200, phone), ...ticks(1300, 2000, ok)]);
R.held = run([...ticks(0, 500, ok), ...ticks(600, 5000, phone)]);
let st = {cond:null, since:0, fired:false, cooldownUntil:0, quietUntil:0};
R.cool = run([...ticks(0, 1500, phone), ...ticks(1600, 3000, ok), ...ticks(3100, 5000, phone), ...ticks(5100, 30000, ok), ...ticks(31000, 33000, phone)], st);
R.glance = run([...ticks(0, 1000, ok), ...ticks(1100, 9000, gone, 500), ...ticks(9500, 10000, ok)]);
R.away = run([...ticks(0, 1000, ok), ...ticks(1100, 30000, gone, 500)]);
R.slouch = run([...ticks(0, 1000, slouch)]);
R.quiet = run([...ticks(0, 5000, phone)], {cond:null, since:0, fired:false, cooldownUntil:0, quietUntil:180000});
console.log(JSON.stringify(R));
"""
res = subprocess.run(["node", "-e", consts + "\n" + step + "\n" + scenarios], capture_output=True, text=True)
R = json.loads(res.stdout) if res.returncode == 0 else {}
check("node ran the page's code", bool(R))
check("phone posture for 600ms: no nudge", R.get("short") == [])
check("held 700ms: one nudge, at 700ms after it started", R.get("held") == [[1300, "phone"]])
check("...and only once however long it's held", len(R.get("held", [])) == 1)
check("a second pickup inside 30s: no nudge; after 30s: nudged again", [e[1] for e in R.get("cool", [])] == ["phone", "phone"]
      and R["cool"][1][0] >= 31700)
check("glancing away for 8s (a notification): not 'away'", R.get("glance") == [])
check("gone for 12s: one 'away'", R.get("away") == [[13100, "away"]])
check("slouch held 700ms: one nudge", R.get("slouch") == [[700, "slouch"]])
check("'give me a minute' quiet: nothing", R.get("quiet") == [])
check("constants as the prompt says: 700ms, 30s, 12s, 3 minutes",
      all(s in consts for s in ("SUSTAIN_MS: 700", "COOLDOWN_MS: 30000", "AWAY_MS: 12000", "QUIET_MS: 180000")))

print("\nTest 2: the ear law and local-only (by reading the code)")
check("the eyes never ask for the microphone", "audio: false" in eyes_block and "audio: true" not in eyes_block)
check("the eyes never start listening", "recognition.start" not in eyes_block and "startListening" not in eyes_block)
check("if the ears are off, it says so", "My ears are off, sir — tap the ear button and just talk." in eyes_block)
check("only the posture's name goes to the server", re.search(r"/eyes/nudge.*?JSON\.stringify\(\{ kind \}\)", eyes_block, re.S) is not None)
check("the only frame sent anywhere is 'look at me'", eyes_block.count("toDataURL") == 1 and "/look" in eyes_block)

print("\nTest 3: the page's phrases")
bits = [re.search(r"const LEADING_FILLERS = \[.*?\];", html, re.S).group(0)]
bits += [re.search(rf"const {n} = /.*?/i;", html).group(0) for n in ("LOOK_RE", "RELIEF_RE", "EYES_ON_RE", "EYES_OFF_RE")]
bits += [re.search(rf"function {f}\(text\) \{{.*?\n\}}", html, re.S).group(0) for f in ("stripLeadingFiller", "commandForm")]
phr = {"look at me": "look", "What do you think of my shirt?": "look", "how do I look": "look",
       "what do you think of this screen": None, "what do you think of this": None,
       "No Jarvis, I need to do something important": "relief", "give me a minute": "relief", "give me a sec": "relief",
       "eyes on": "on", "watch me": "on", "eyes off": "off", "stop watching me": "off", "give me a minute to think about it": None}
js = "\n".join(bits) + f"""
console.log(JSON.stringify({json.dumps(list(phr))}.map(p => {{ const c = commandForm(p);
  return RELIEF_RE.test(c) ? 'relief' : EYES_ON_RE.test(c) ? 'on' : EYES_OFF_RE.test(c) ? 'off' : LOOK_RE.test(c) ? 'look' : null; }})));"""
r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
got = json.loads(r.stdout) if r.returncode == 0 else []
for (p, want), g in zip(phr.items(), got):
    check(f"{p!r} -> {want}", g == want)
check("node ran", len(got) == len(phr))

print("\nTest 4: the server")
real = {k: getattr(server, k) for k in ("load_config", "call_brain")}
KEY = {"k": "PUT-YOUR-KEY-HERE"}
server.load_config = lambda: {"openrouter_api_key": KEY["k"]}
seen = {}
def brain(config, messages):
    seen["m"] = messages
    return "A fine shirt, sir, if a little optimistic for a Wednesday.", "stand-in/vision", None
server.call_brain = brain
httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
PORT = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()
def post(p, b):
    return json.loads(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}{p}",
        data=json.dumps(b).encode(), headers={"Content-Type": "application/json"}, method="POST"), timeout=10).read())
try:
    fs._quiet_until = 0
    fs.stop_session()
    r = post("/eyes/nudge", {"kind": "phone"})
    check("no focus session: a phone line, not counted", r["line"] in fs.PHONE_LINES and not r["counted_as_drift"])
    fake_tab = lambda: {"browser": "Chrome", "title": "x", "url": "https://docs.example.com/"}
    s = fs.start_session("the eyes test", 5, get_tab_fn=fake_tab, get_window_fn=None)
    time.sleep(1.2)
    before = s.drift_count
    r = post("/eyes/nudge", {"kind": "phone"})
    check("in a focus session: the phone counts as a drift, own line pool", r["counted_as_drift"]
          and s.drift_count == before + 1 and r["line"] in fs.PHONE_LINES)
    fs.set_drill_mode(True)
    check("drill sergeant has phone lines too", post("/eyes/nudge", {"kind": "phone"})["line"] in fs.DRILL_PHONE_LINES)
    fs.set_drill_mode(False)
    check("report still aggregates only (no new ledger field)", set(s.report()) == fs.LEDGER_ALLOWED_KEYS)
    s._announcements.extend([("callout", "Sir, a callout"), ("info", "Locked on, sir.")])
    r = post("/quiet", {})
    check("'give me a minute' -> quiet, said once", r["spoken"] == "Very good, sir. Not a word for three minutes.")
    check("quiet: eyes nudges return nothing", post("/eyes/nudge", {"kind": "slouch"})["line"] is None)
    check("quiet: focus callouts dropped, other lines kept", s.pop_announcements() == ["Locked on, sir."])
    fs._quiet_until = time.time() - 1
    check("after the quiet: nudges again", post("/eyes/nudge", {"kind": "slouch"})["line"] in fs.SLOUCH_LINES)
    fs.stop_session()
    try:
        post("/eyes/nudge", {"kind": "selfie"}); bad = False
    except urllib.error.HTTPError as e:
        bad = e.code == 400
    check("unknown posture refused", bad)
    check("look with eyes off: says so", post("/look", {"question": "look at me"})["error"].startswith("My eyes are off"))
    check("look with no key: says so", "No API key" in post("/look", {"question": "x", "image_base64": "AAAA"})["error"])
    KEY["k"] = "sk-test"
    r = post("/look", {"question": "what do you think of my shirt?", "image_base64": "AAAA"})
    check("look: one frame to the brain, dry answer back", r["ok"] and r["answer"].startswith("A fine shirt"))
    check("the look prompt: live webcam photo, dry wit, one to three sentences",
          "live webcam photo of the user at their desk" in seen["m"][0]["content"]
          and "one to three sentences" in seen["m"][0]["content"])
finally:
    httpd.shutdown()
    for k, v in real.items(): setattr(server, k, v)
    fs._quiet_until = 0
print(f"\n{PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)
