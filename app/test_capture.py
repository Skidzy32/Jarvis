"""
Tests for Personal OS Phase 2 (2.4.0): capture phrases and modes, the
INBOX, paper notes, the F1 follow-up -- and that the page's JavaScript
agrees with the server about which phrases are captures.

Runs a real server.Handler on a spare port against a THROWAWAY notes
folder; the vision model is stood in for (no key needed, nothing sent
anywhere). Your real notes/ are never touched.
Run: python3 test_capture.py
"""

import base64
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import urllib.request
import zlib
from http.server import ThreadingHTTPServer

import inbox
import records
import server

PASS = 0
FAIL = 0
ROOT = os.path.dirname(os.path.abspath(__file__))


def check(label, condition):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK]   {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}")


def png_bytes(w=4, h=3):
    """A real, valid PNG made from scratch."""
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


# ---------------------------------------------------------------------------
print("Test 1: the new capture phrases (server side)")
cases = [
    ("Remind me to call Dave about closing", "call Dave about closing"),
    ("remind me that the MOT is due in March", "the MOT is due in March"),
    ("Make a note that the boiler code is 4471", "the boiler code is 4471"),
    ("make a note of the WiFi password on the router", "the WiFi password on the router"),
    ("Jot this down: buy lightbulbs", "buy lightbulbs"),
    ("write that down, Sam's birthday is the 3rd", "Sam's birthday is the 3rd"),
    ("add to my inbox: sort the spreadsheet tomorrow", "sort the spreadsheet tomorrow"),
    ("Capture this: I felt constantly behind today", "I felt constantly behind today"),
    ("idea: a Minecraft server for the lads", "a Minecraft server for the lads"),
    ("Thought: astronomy is weirdly interesting lately", "astronomy is weirdly interesting lately"),
    ("um, remember that Priya prefers green tea", "Priya prefers green tea"),
    ("remember to ask Dave about closing", "ask Dave about closing"),
]
for said, want in cases:
    got = server.capture_content_for_mode(said, "phrase")
    check(f"{said!r} -> {want!r}", got == want)
not_captures = ["I need to know what the budget is", "what's the plan for today",
                "can you remind me what we said about Acme", "note the date on that please",
                "write me a poem", "thoughts on the Acme rebrand?"]
for q in not_captures:
    check(f"a question is NOT a capture: {q!r}",
          not any(p.match(server.strip_leading_filler(q)) for p in server.REMEMBER_PATTERNS))

print("\nTest 2: capture mode and 'save that' keep your words exactly")
said = "Work was fucking chaotic today and I felt like I was constantly behind."
check("capture mode: kept word for word, swearing and full stop included",
      server.capture_content_for_mode(f"  {said}  ", "capture-mode") == said)
check("save-previous: same", server.capture_content_for_mode(said, "save-previous") == said)
check("capture mode + a trigger phrase: the phrase is still taken off",
      server.capture_content_for_mode("remember that the bins go out Tuesday", "capture-mode") == "the bins go out Tuesday")
check("phrase mode: unchanged from before (trailing full stop trimmed as it always was)",
      server.capture_content_for_mode("remember that the bins go out Tuesday.", "phrase") == "the bins go out Tuesday")

print("\nTest 2b: names offered for their own note (fixes found in 2.4.0 testing)")
_d = tempfile.mkdtemp()
_p = os.path.join(_d, "x.md")
for body, want, why in [
    ("# Ask Dave about closing\n\nCaptured 2026-09-22.\n\nask Dave about closing\n", "Dave",
     "'Ask' (capitalised by Jarvis's own title) is not a name; Dave is"),
    ("# Priya prefers green tea\n\nCaptured 2026-09-22.\n\nPriya prefers green tea\n", "Priya",
     "a name at the start of the sentence is still found (the original example)"),
    ("# Call the dentist\n\nCaptured 2026-09-22.\n\ncall the dentist\n", None,
     "'Captured' from Jarvis's own date line is not a name"),
]:
    open(_p, "w").write(body)
    check(why, server.find_link_suggestions({"id": 0, "path": _p}, {"nodes": []})[1] == want)
shutil.rmtree(_d)

# ---------------------------------------------------------------------------
print("\nTest 3: the page's JavaScript agrees with the server")
html = open(os.path.join(ROOT, "viewer", "index.html"), encoding="utf-8").read()
js_bits = []
for name in ("LEADING_FILLERS", "REMEMBER_PATTERNS"):
    m = re.search(rf"const {name} = \[.*?\];", html, re.S)
    js_bits.append(m.group(0))
for name in ("CAPTURE_ON_RE", "CAPTURE_OFF_RE", "SAVE_PREVIOUS_RE", "INBOX_QUERY_RE", "PAPER_RE"):
    m = re.search(rf"const {name} = /.*?/i;", html)
    js_bits.append(m.group(0))
for fn in ("stripLeadingFiller", "matchRememberTrigger", "commandForm"):
    m = re.search(rf"function {fn}\(text\) \{{.*?\n\}}", html, re.S)
    js_bits.append(m.group(0))

phrases = [c[0] for c in cases] + not_captures + [
    "start capturing", "Capture mode.", "capture mode on", "brain dump", "stop capturing",
    "done capturing!", "capture off", "save that", "Save that to my inbox.", "that was a note",
    "remember that", "keep that one", "what's in my inbox?", "show me my inbox", "inbox",
    "how many things are in my inbox", "process this page", "paper note",
    "save that for later please", "what's in my inbox for today", "stop capturing my thoughts",
]
js = "\n".join(js_bits) + f"""
const phrases = {json.dumps(phrases)};
const out = phrases.map(p => {{
  const c = commandForm(p);
  const kind = CAPTURE_ON_RE.test(c) ? 'on' : CAPTURE_OFF_RE.test(c) ? 'off'
    : SAVE_PREVIOUS_RE.test(c) ? 'save' : INBOX_QUERY_RE.test(c) ? 'inbox'
    : PAPER_RE.test(c) ? 'paper' : null;
  return {{ phrase: p, remember: matchRememberTrigger(p), kind }};
}});
console.log(JSON.stringify(out));
"""
res = subprocess.run(["node", "-e", js], capture_output=True, text=True)
if res.returncode != 0:
    check(f"node ran the page's code ({res.stderr.strip()[:200]})", False)
    js_out = []
else:
    js_out = json.loads(res.stdout)
expected_kind = {
    "start capturing": "on", "Capture mode.": "on", "capture mode on": "on", "brain dump": "on",
    "stop capturing": "off", "done capturing!": "off", "capture off": "off",
    "save that": "save", "Save that to my inbox.": "save", "that was a note": "save",
    "remember that": "save", "keep that one": "save",
    "what's in my inbox?": "inbox", "show me my inbox": "inbox", "inbox": "inbox",
    "how many things are in my inbox": "inbox",
    "process this page": "paper", "paper note": "paper",
}
for row in js_out:
    p = row["phrase"]
    py = any(pat.match(server.strip_leading_filler(re.sub(r'^[\s"\'“”‘’.,;:!?-]+', "", p.strip())))
             for pat in server.REMEMBER_PATTERNS)
    if p in expected_kind:
        check(f"page reads {p!r} as '{expected_kind[p]}'", row["kind"] == expected_kind[p])
    elif p in ("save that for later please", "what's in my inbox for today", "stop capturing my thoughts"):
        check(f"page doesn't treat {p!r} as a command (goes on to be answered)", row["kind"] is None)
    else:
        same = (row["remember"] is not None) == py
        check(f"page and server agree whether {p!r} is a capture", same and row["kind"] is None)

# ---------------------------------------------------------------------------
print("\nTest 4: a real server on a throwaway notes folder")
W = tempfile.mkdtemp(prefix="jarvis-cap-")
NOTES = os.path.join(W, "notes")
os.makedirs(os.path.join(W, "viewer"))
real = {k: getattr(server, k) for k in ("NOTES_DIR", "GRAPH_DATA_PATH", "load_config", "call_brain")}
real_run = server.subprocess.run
server.NOTES_DIR = NOTES
server.GRAPH_DATA_PATH = os.path.join(W, "viewer", "graph-data.js")
server.subprocess.run = lambda cmd, cwd=None, **kw: real_run(cmd, cwd=W, **kw)
KEY = {"k": "PUT-YOUR-KEY-HERE"}
server.load_config = lambda: {"openrouter_api_key": KEY["k"]}
VISION = {"reply": None, "fail": False, "seen": None}


def fake_brain(config, messages):
    VISION["seen"] = messages
    if VISION["fail"]:
        raise RuntimeError("Every model in the chain failed")
    return VISION["reply"], "test/vision-model", None


server.call_brain = fake_brain
httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
PORT = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()


def post(path, payload):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


def get(path):
    return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=10).read())


try:
    server.startup_rebuild()
    inbox.seed_followups(NOTES)          # 4.0.0: the server no longer adds the owner's F1/F2; this scenario uses them
    inbox_now = get("/inbox")
    f1_item = next((i for i in inbox_now["items"] if "OpenRouter" in i["title"]), None)
    f2_item = next((i for i in inbox_now["items"] if "reading and processing images" in i["title"]), None)
    check("first start: the F1 and F2 follow-ups are the only things in the inbox",
          inbox_now["count"] == 2 and f1_item and f2_item)
    f2 = records.find_by_path(os.path.join(NOTES, f2_item["path"]), NOTES)
    check("F2's record: source 'jarvis', your words, points to proposal P4",
          f2["followup"] == "F2" and "image reading and processing" in f2["original_input"]
          and "proposal P4" in open(os.path.join(NOTES, f2_item["path"])).read())
    inbox_now["items"][0] = f1_item
    f1 = records.find_by_path(os.path.join(NOTES, inbox_now["items"][0]["path"]), NOTES)
    check("F1's record: source 'jarvis', your own words kept, marked as follow-up F1",
          f1["source"] == "jarvis" and f1["followup"] == "F1" and "remind me about once completed" in f1["original_input"])
    body = open(os.path.join(NOTES, inbox_now["items"][0]["path"])).read()
    check("F1 says honestly that Jarvis can't change the setting itself", "Jarvis can't do it for you" in body)

    r = post("/remember", {"message": "Remind me to call Dave about closing", "source": "spoken"})
    check("'remind me to...' is captured", r["ok"] and r["node"]["label"] == "Call Dave about closing")
    check("...and the reply says plainly it can't remind at a time yet", "can't yet remind you" in r["confirmation"])

    said = "Work was fucking chaotic today and I felt like I was constantly behind."
    r = post("/remember", {"message": said, "source": "typed", "mode": "capture-mode"})
    note = open(r["node"]["path"]).read()
    check("capture mode: saved word for word in the note", note.endswith(f"\n\n{said}\n"))
    check("capture mode: the short reply", r["confirmation"] == "In the inbox, sir.")
    rec = records.find_by_path(r["node"]["path"], NOTES)
    check("its record: exact words, typed, and how it arrived",
          rec["original_input"] == said and rec["source"] == "typed"
          and rec["history"][0]["detail"]["how"] == "capture-mode")

    r = post("/remember", {"message": "Would be cool to visit Iceland", "source": "typed", "mode": "save-previous"})
    check("'save that' saves the earlier message", r["ok"] and "Saved that to your inbox" in r["confirmation"]
          and open(r["node"]["path"]).read().endswith("Would be cool to visit Iceland\n"))
    r = post("/remember", {"message": "x", "mode": "rm -rf"})
    check("an unknown mode is treated as a normal capture, not trusted", r["ok"])

    inbox_now = get("/inbox")
    check("inbox: 6 things, newest first", inbox_now["count"] == 6
          and inbox_now["items"][0]["created"] >= inbox_now["items"][-1]["created"])
    check("inbox previews are the note's own words, not its title/date lines",
          any(i["preview"] == said for i in inbox_now["items"]))
    check("the spoken summary", inbox_now["summary"].startswith("Six things in your inbox, sir. The newest:"))
    check("summary wording for 0 and 1",
          inbox.spoken_summary([]) == "Your inbox is empty, sir."
          and inbox.spoken_summary([{"title": "A"}]) == "One thing in your inbox, sir: 'A'.")

    # ---- paper ----
    photo = png_bytes()
    b64 = base64.b64encode(photo).decode()
    r = post("/paper/extract", {"image_base64": b64})
    check("no key: not read, and told you can type it instead",
          not r["ok"] and r["reason"] == "no_key" and "type what it says" in r["error"])
    check("...and nothing was saved", not os.path.exists(os.path.join(NOTES, "paper")))
    KEY["k"] = "sk-test"
    VISION["reply"] = "Rent due Fri\nCall mum [?]\n[arrow from 'rent' to 'budget']\nfuckin tired"
    r = post("/paper/extract", {"image_base64": b64})
    check("with a model: the reading comes back, and still nothing is saved",
          r["ok"] and r["text"].startswith("Rent due Fri") and not os.path.exists(os.path.join(NOTES, "paper")))
    sys_prompt = VISION["seen"][0]["content"]
    check("the model is told to keep your words exactly, swearing included, and not tidy",
          "word for word" in sys_prompt and "swearing" in sys_prompt and "Do not correct, summarise, soften" in sys_prompt)
    check("the photo went to the model as a PNG picture",
          VISION["seen"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))
    VISION["fail"] = True
    r = post("/paper/extract", {"image_base64": b64})
    check("model fails: said plainly, with the type-it-yourself way out",
          not r["ok"] and r["reason"] == "model" and "type what it says" in r["error"])
    VISION["fail"] = False
    check("not a picture -> refused",
          not post("/paper/extract", {"image_base64": base64.b64encode(b"hello, not an image").decode()})["ok"])
    heic = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 50
    check("an iPhone HEIC photo -> refused with the reason",
          "HEIC" in post("/paper/extract", {"image_base64": base64.b64encode(heic).decode()})["error"])

    extracted = VISION["reply"]
    final = "Rent due Fri\nCall mum\n[arrow from 'rent' to 'budget']\nfuckin tired"
    r = post("/paper/save", {"photo_base64": b64, "final_text": final,
                             "extracted_text": extracted, "model_used": "test/vision-model"})
    check("save: ok, and the galaxy grows", r["ok"] and r["node"] is not None and "photo is kept" in r["confirmation"])
    folder = os.path.join(NOTES, "paper")
    photos = [f for f in os.listdir(folder) if f.endswith(".png")]
    check("the photo is kept byte for byte", len(photos) == 1 and open(os.path.join(folder, photos[0]), "rb").read() == photo)
    note_path = r["node"]["path"]
    text = open(note_path).read()
    check("the note holds what you confirmed, and names its photo",
          text.startswith("# Rent due Fri\n\nCaptured ") and f"Photo: {photos[0]}" in text and text.endswith(final + "\n"))
    rec = records.find_by_path(note_path, NOTES)
    interp = rec["interpretations"][0]
    check("record: source paper, the photo and its fingerprint",
          rec["source"] == "paper" and rec["source_file"] == f"paper/{photos[0]}"
          and len(rec["source_file_sha256"]) == 64)
    check("the AI's reading kept beside it as an interpretation, not over it",
          interp["kind"] == "transcription" and interp["value"] == extracted and interp["model"] == "test/vision-model")
    check("marked confirmed by you, and that you corrected it; no invented confidence",
          interp["confirmed"] is True and interp["corrected_by_you"] is True and interp["confidence"] is None)
    check("your confirmed text is the record's original input", rec["original_input"] == final)
    check("it's in the inbox, marked as having a photo",
          any(i["path"] == records.rel_path(note_path, NOTES) and i["photo"] for i in get("/inbox")["items"]))

    r = post("/paper/save", {"photo_base64": b64, "final_text": "Typed it myself", "extracted_text": None})
    rec = records.find_by_path(r["node"]["path"], NOTES)
    check("typed it yourself (no reading): saved with no interpretation",
          r["ok"] and rec["interpretations"] == [] and rec["history"][0]["detail"]["read_by"] == "you typed it")
    before = sorted(os.listdir(folder))
    r = post("/paper/save", {"photo_base64": b64, "final_text": "   "})
    check("empty text -> not saved, nothing written", not r["ok"] and sorted(os.listdir(folder)) == before)
    r = post("/paper/save", {"photo_base64": "bm90IGFuIGltYWdl", "final_text": "hello"})
    check("bad photo -> not saved, nothing written", not r["ok"] and sorted(os.listdir(folder)) == before)
    check("records healthy after all that", records.diag(NOTES)["healthy"])

    # ---- F1 only once ----
    f1_path = os.path.join(NOTES, "captures", "check-openrouter-privacy-settings-2026-09-22.md")
    check("restart: F1 not added twice", inbox.seed_followups(NOTES) == [] and os.path.exists(f1_path))
    os.remove(f1_path)
    server.startup_rebuild()
    check("you deleted F1 -> it stays deleted (not re-added)", not os.path.exists(f1_path))
finally:
    httpd.shutdown()
    server.subprocess.run = real_run
    for k, v in real.items():
        setattr(server, k, v)
    shutil.rmtree(W, ignore_errors=True)

print(f"\n{PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)
