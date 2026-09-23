"""
Tests for Personal OS Phase 9 (3.0.0): automatic upkeep.
Fixed dates, stand-in model, throwaway folders; your real notes are never touched.
Run: python3 test_maintenance.py
"""
import datetime, json, os, shutil, tempfile, threading, urllib.request
from http.server import ThreadingHTTPServer
import loops, maintenance, records, reviews, server, sorting
from testkit import StandIn, reading, make_notes, note_hashes, rec_for

PASS = FAIL = 0


def check(label, ok):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
    print(f"  [{'OK' if ok else 'FAIL'}]{'   ' if ok else ' '}{label}")


N = tempfile.mkdtemp(prefix="jarvis-maint-")
texts = ["Call the dentist about the filling", "call the dentist about the filling!",
         "Fix the garden gate", "Feeling really flat this week", "Maybe learn Spanish", "The wifi password is on the router"]
paths = make_notes(N, texts)
# the two dentist notes are made in the same second; say which is older, or the test is a coin toss
_sc = rec_for(N, paths[0]); _sc["created"] = "2026-01-01T09:00:00"; records.save(_sc, N)
before = note_hashes(N)
sorting.sort_inbox(StandIn({
    "dentist": reading(home="NOW", kinds=["task"], intention="act"),
    "gate": reading(home="NOW", kinds=["task"], intention="act"),
    "flat": reading(home="JOURNAL", kinds=["reflection"], memory="current_state", temporary=True),
    "Spanish": reading(home="SOMEDAY", kinds=["idea"], confidence=0.55),
    "wifi": reading(home="KNOWLEDGE"),
}), {}, notes_dir=N)
loops.update_loop(rec_for(N, paths[2])["id"], "done", notes_dir=N)

print("Test 1: suggestions")
later = datetime.date.today() + datetime.timedelta(days=40)
r = maintenance.report(N, today=later)
check("the two dentist notes: 'same thing?', keeping the older", len(r["duplicates"]) == 1
      and r["duplicates"][0]["keep"] == rec_for(N, paths[0])["id"] and r["duplicates"][0]["dup"] == rec_for(N, paths[1])["id"])
arch = {a["title"][:12]: a["reason"] for a in r["archive"]}
check("archive: the done gate (14+ days) and the passing mood (30+ days)", set(arch) == {"Fix the gard", "Feeling real"}
      and arch["Feeling real"].startswith("described as a passing thing"))
check("second look: the unsure, unconfirmed Spanish reading", [s["title"][:13] for s in r["second_look"]] == ["Maybe learn S"])
check("unconnected count", r["unconnected"] >= 4)
check("spoken, calm", r["spoken"].startswith("A little tidying, when you have a moment: 1 possible duplicate, 2 things"))
check("nothing suggested on day one (too soon)", maintenance.report(N)["archive"] == []
      and maintenance.report(N)["second_look"] == [])

print("\nTest 2: acting on them never deletes or edits a note")
line = maintenance.apply("merge", keep=r["duplicates"][0]["keep"], dup=r["duplicates"][0]["dup"], notes_dir=N)
dup = rec_for(N, paths[1])
check("merge: newer one archived, marked duplicate, linked both ways, its loop dropped",
      dup["home"] == "ARCHIVE" and dup["duplicate_of"] == rec_for(N, paths[0])["id"] and dup["loop"]["state"] == "dropped"
      and any(l["reason"] == "same thing" for l in rec_for(N, paths[0])["links"]) and "Neither was deleted" in line)
maintenance.apply("archive", record_id=rec_for(N, paths[2])["id"], notes_dir=N)
check("archive: filed in Archive", rec_for(N, paths[2])["home"] == "ARCHIVE")
r2 = maintenance.report(N, today=later)
maintenance.apply("keep", key=r2["archive"][0]["key"], notes_dir=N)
check("'keep' -> not suggested again", maintenance.report(N, today=later)["archive"] == [])
check("archived notes aren't suggested again either", maintenance.report(N, today=later)["duplicates"] == [])
try:
    maintenance.apply("delete", record_id="x", notes_dir=N); refused = False
except ValueError:
    refused = True
check("'delete' isn't an action at all", refused)
check("NOTES UNCHANGED (all files still there, byte for byte)", note_hashes(N) == before)

print("\nTest 3: once-a-day auto-sort, within the free quota")
A = tempfile.mkdtemp(prefix="jarvis-maint2-")
make_notes(A, [f"thing {i}" for i in range(12)])
brain = StandIn({"thing": reading()})
cfg = lambda: {"openrouter_api_key": "sk-test"}
now = datetime.datetime(2026, 9, 23, 6, 30)
check("before 07:00: nothing", maintenance.auto_tick(now, brain, cfg, "PUT-YOUR-KEY-HERE", notes_dir=A) is None)
res = maintenance.auto_tick(now.replace(hour=9), brain, cfg, "PUT-YOUR-KEY-HERE", notes_dir=A)
check("09:00: ONE batch (8 of 12), ONE request", res["sorted"] == 8 and brain.calls == 1 and len(sorting.waiting(A)) == 4)
check("...and not again that day", maintenance.auto_tick(now.replace(hour=15), brain, cfg, "PUT-YOUR-KEY-HERE", notes_dir=A) is None
      and brain.calls == 1)
res = maintenance.auto_tick(now + datetime.timedelta(days=1, hours=3), brain, cfg, "PUT-YOUR-KEY-HERE", notes_dir=A)
check("next day: the rest", res["sorted"] == 4 and brain.calls == 2)
B = tempfile.mkdtemp(prefix="jarvis-maint3-")
make_notes(B, ["x one"])
b2 = StandIn({"x": reading()})
check("auto_sort switched off -> no request", maintenance.auto_tick(now.replace(hour=9), b2, cfg, "P", notes_dir=B,
      settings={"auto_sort": False})["note"] == "auto_sort is off" and b2.calls == 0)
C = tempfile.mkdtemp(prefix="jarvis-maint4-")
make_notes(C, ["x one"])
check("no key -> says so, no request", maintenance.auto_tick(now.replace(hour=9), b2, lambda: {"openrouter_api_key": "P"}, "P",
      notes_dir=C)["error"] == "no key" and b2.calls == 0)
check("the day's result is kept for /diag", maintenance.load_state(A)["last_report"]["sorted"] == 4)
check("settings: auto_sort defaults on and is validated", reviews.DEFAULT_SETTINGS["auto_sort"] is True)

print("\nTest 4: weekly review line + HTTP")
check("weekly review has a 'Tidy up' line when there's something",
      "Tidy up" in [s["title"] for s in reviews.build("weekly", N, today=later)["sections"]])
W = tempfile.mkdtemp()
real = {k: getattr(server, k) for k in ("NOTES_DIR", "GRAPH_DATA_PATH")}
real_run = server.subprocess.run
server.NOTES_DIR, server.GRAPH_DATA_PATH = N, os.path.join(W, "viewer", "graph-data.js")
os.makedirs(os.path.join(W, "viewer"))
server.subprocess.run = lambda cmd, cwd=None, **kw: real_run(cmd, cwd=W, **kw)
httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
PORT = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()
def post(p, b): return json.loads(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}{p}",
    data=json.dumps(b).encode(), headers={"Content-Type": "application/json"}, method="POST"), timeout=20).read())
try:
    ib = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/inbox", timeout=10).read())
    check("/inbox carries the tidy-up report", "tidy" in ib and "duplicates" in ib["tidy"])
    check("/maintenance/apply keep", post("/maintenance/apply", {"action": "keep", "key": "look:x"})["ok"])
    check("/maintenance/apply bad", not post("/maintenance/apply", {"action": "archive", "record_id": "nope"})["ok"])
finally:
    httpd.shutdown(); server.subprocess.run = real_run
    for k, v in real.items(): setattr(server, k, v)
for d in (N, A, B, C, W):
    shutil.rmtree(d, ignore_errors=True)
print(f"\n{PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)
