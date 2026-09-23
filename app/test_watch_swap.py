"""
Tests for Prompt 14 (the stare / screen watch) and Prompt 15 (the swap gets
its lines). The page's own watch logic runs in Node; the server for real,
with a stand-in brain. Run: python3 test_watch_swap.py
"""
import json, os, re, subprocess, threading, time, urllib.request
from http.server import ThreadingHTTPServer
import focus_overlay, focus_session as fs, server

PASS = FAIL = 0
ROOT = os.path.dirname(os.path.abspath(__file__))


def check(label, ok):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
    print(f"  [{'OK' if ok else 'FAIL'}]{'   ' if ok else ' '}{label}")


html = open(os.path.join(ROOT, "viewer", "index.html"), encoding="utf-8").read()
block = html.split("// ---------- Prompt 14: the stare (screen watch) ----------")[1].split("// ---------- Prompt 13: the eyes")[0]
js = "\n".join([re.search(r"const WATCH = \{.*?\};", block, re.S).group(0),
                re.search(r"function frameDiff\(a, b\) \{.*?\n\}", block, re.S).group(0),
                re.search(r"function watchStep\(st, changed, now\) \{.*?\n\}", block, re.S).group(0)])

print("Test 1: the stare (the page's own code, in Node)")
scen = js + r"""
const R = {};
const run = (samples, st) => { st = st || {stillSince:null, cooldownUntil:0, quietUntil:0}; const o=[];
  for (const [t, ch] of samples) for (const e of watchStep(st, ch, t)) o.push(t); return o; };
const every5 = (from, to, ch) => { const a=[]; for (let t=from; t<=to; t+=5000) a.push([t, ch]); return a; };
R.still = run([[0, true], ...every5(5000, 70000, false)]);
R.moving = run([[0, true], ...every5(5000, 120000, true)]);
R.cool = run([[0, true], ...every5(5000, 300000, false)]);
R.reset = run([[0, true], ...every5(5000, 55000, false), [60000, true], ...every5(65000, 115000, false)]);
R.quiet = run([[0, true], ...every5(5000, 90000, false)], {stillSince:null, cooldownUntil:0, quietUntil:200000});
R.diff = [frameDiff([10,10,10],[10,10,10]), frameDiff([0,0,0],[30,30,30]), frameDiff([1],[1,2])];
console.log(JSON.stringify(R));
"""
r = subprocess.run(["node", "-e", scen], capture_output=True, text=True)
R = json.loads(r.stdout) if r.returncode == 0 else {}
check("node ran", bool(R))
check("screen still for 60s -> ONE nudge at 60s", R.get("still") == [60000])
check("a screen that keeps changing -> never", R.get("moving") == [])
check("still for 5 minutes -> nudges 3 minutes apart, not every minute", R.get("cool") == [60000, 240000])
check("a change at 60s resets the stillness clock (so no nudge by 115s)", R.get("reset") == [])
check("'give me a minute' -> quiet", R.get("quiet") == [])
check("thumbnail diff: same=0, different>0, mismatched=max", R.get("diff") == [0, 30, 255])
check("constants: 5s samples, 60s stare, 3-minute cooldown",
      all(s in block for s in ("SAMPLE_MS: 5000", "STARE_MS: 60000", "COOLDOWN_MS: 180000")))
check("the picker opens on Entire Screen", "displaySurface: 'monitor'" in block)
check("tab share -> said honestly, steered to Entire Screen", "That's a single tab, sir." in block and "pick Entire Screen" in block)
check("screen only: never touches the camera", "getUserMedia" not in block)
check("only the stare nudge sends a frame", block.count("captureScreenFrameBase64()") == 1 and "/watch/nudge" in block)

print("\nTest 2: the page's phrases")
bits = [re.search(r"const LEADING_FILLERS = \[.*?\];", html, re.S).group(0)]
bits += [re.search(rf"const {n} = /.*?/i;", html).group(0) for n in ("WATCH_ON_RE", "WATCH_OFF_RE", "EYES_ON_RE")]
bits += [re.search(rf"function {f}\(text\) \{{.*?\n\}}", html, re.S).group(0) for f in ("stripLeadingFiller", "commandForm")]
phr = {"watch my screen": "on", "Jarvis, watch my screen.": "on", "keep an eye on my screen": "on",
       "stop watching my screen": "off", "stop watching": "off", "watch me": "eyes", "what's on my screen": None}
code = "\n".join(bits) + f"""
console.log(JSON.stringify({json.dumps(list(phr))}.map(p => {{ const c = commandForm(p);
  return WATCH_ON_RE.test(c) ? 'on' : WATCH_OFF_RE.test(c) ? 'off' : EYES_ON_RE.test(c) ? 'eyes' : null; }})));"""
r = subprocess.run(["node", "-e", code], capture_output=True, text=True)
got = json.loads(r.stdout) if r.returncode == 0 else []
for (p, want), g in zip(phr.items(), got):
    check(f"{p!r} -> {want}", g == want)

print("\nTest 3: the card's face")
check("watching, no session: 'Watching your screen'", focus_overlay.overlay_text({"status": {"active": False}, "watching": True}, None)[:2]
      == ("👁 Jarvis", "Watching your screen"))
check("watching during a session: the eye on the timer", focus_overlay.overlay_text(
      {"status": {"active": True, "task": "x", "remaining_seconds": 60}, "watching": True}, None)[0] == "👁 ⏱ 01:00")
check("not watching: unchanged", focus_overlay.overlay_text({"status": {"active": False}}, None)[:2] == ("Jarvis", "No focus session running"))

print("\nTest 4: Prompt 15 -- the swap's lines")
check("pinned names: astra, gpt-6-astra, gpt 6 astra, gpt-6", {"astra", "gpt-6-astra", "gpt 6 astra", "gpt-6"}
      <= next(e for e in server.MODEL_REGISTRY if "astra" in e["slug"])["aliases"])
astra = [server.curated_swap_line("openai/gpt-6-astra") for _ in range(6)]
check("Astra: six lines that rotate, never repeating in a row", len(set(astra)) == 6
      and astra[0] == "New brain fitted, sir — GPT-6 Astra. Do try to keep up.")
check("every registry brain has its own pool", all(server.curated_swap_line(e["slug"]) for e in server.MODEL_REGISTRY))
src = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
handler = src.split("def _handle_model_swap")[1].split("\n    def ")[0]
check("ONE VOICE: every swap confirmation in the handler goes through swap_line()",
      handler.count("swap_line(") == 4 and "New brain fitted" not in handler)
real_once, real_cfg = server._call_openrouter_once, server.load_config
server.load_config = lambda: {"openrouter_api_key": "sk-test"}
try:
    server._call_openrouter_once = lambda k, m, msgs: ("I'm the new brain, sir, and I've already alphabetised your regrets.", m)
    check("no curated pool -> the model's own one-line intro", server.swap_line("acme/brainy-9", "Brainy 9")
          == "I'm the new brain, sir, and I've already alphabetised your regrets.")
    server._call_openrouter_once = lambda k, m, msgs: ("", m)
    check("model says nothing (reasoning models) -> the pretty-name fallback", server.swap_line("acme/brainy-9", "Brainy 9")
          == "New brain fitted, sir — Brainy 9.")
    def boom(k, m, msgs): raise RuntimeError("down")
    server._call_openrouter_once = boom
    check("model unreachable -> fallback, no crash", server.swap_line("acme/brainy-9", "Brainy 9", ", the latest")
          == "New brain fitted, sir — Brainy 9, the latest.")
finally:
    server._call_openrouter_once, server.load_config = real_once, real_cfg

print("\nTest 5: over HTTP (watch state, stare nudge, two swaps)")
KEY = {"k": "PUT-YOUR-KEY-HERE"}
real = {k: getattr(server, k) for k in ("load_config", "call_brain")}
server.load_config = lambda: {"openrouter_api_key": KEY["k"]}
seen = {}
def brain(config, messages):
    seen["m"] = messages
    return "You've been looking at that same error for a minute, sir; line 42 wants a closing bracket.", "stand-in/vision", None
server.call_brain = brain
httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
PORT = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()
def post(p, b): return json.loads(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}{p}",
    data=json.dumps(b).encode(), headers={"Content-Type": "application/json"}, method="POST"), timeout=10).read())
def status(): return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/focus/status", timeout=10).read())
try:
    fs._quiet_until = 0
    check("not watching by default", status()["watching"] is False)
    post("/watch/state", {"on": True})
    check("watching once the page reports in", status()["watching"] is True)
    server.SCREEN_WATCH["last_seen"] = time.time() - 30
    check("the page goes quiet for 20s+ (tab closed) -> card stops claiming to watch", status()["watching"] is False)
    check("stare nudge, no key: says so", "No API key" in post("/watch/nudge", {"image_base64": "AAAA"})["error"])
    KEY["k"] = "sk-test"
    r = post("/watch/nudge", {"image_base64": "AAAA"})
    check("stare nudge: one frame, one dry useful line", r["ok"] and "line 42" in r["line"])
    check("the stare prompt: stuck, one useful nudge, don't guess", "may be stuck" in seen["m"][0]["content"]
          and "ONE genuinely useful nudge" in seen["m"][0]["content"])
    fs.set_quiet(60)
    check("'give me a minute' silences the stare too", post("/watch/nudge", {"image_base64": "AAAA"})["line"] is None)
    fs._quiet_until = 0
    a = post("/model", {"message": "switch to astra"})["confirmation"]
    b = post("/model", {"message": "switch to gpt 6 astra"})["confirmation"]
    check("two swaps, two different curated lines", a != b and a.startswith(("New brain", "GPT-6", "Astra")) and "Astra" in b)
    check("back to free: its own line", "free" in post("/model", {"message": "go back to your normal brain"})["confirmation"].lower())
finally:
    httpd.shutdown()
    for k, v in real.items(): setattr(server, k, v)
    server.runtime_override_model = None
    fs._quiet_until = 0
print(f"\n{PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)
