"""
Tests for records.py (Personal OS Phase 1, 2.3.0) -- and for how server.py
uses it: capture, link, file-under, person notes, empty folder.

Everything runs in a throwaway copy of a notes folder; your real notes/
and notes/.jarvis are never touched.
Run: python3 test_records.py
"""

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time

import records

PASS = 0
FAIL = 0


def check(label, condition):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK]   {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}")


def note_bytes(root):
    """Exact bytes of every note, so 'never writes a note' can be proven."""
    out = {}
    for rel in records.note_files(root):
        with open(records.abs_path(rel, root), "rb") as f:
            out[rel] = hashlib.sha256(f.read()).hexdigest()
    return out


def write(root, rel, text, mtime=None):
    p = records.abs_path(rel, root)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    if mtime:
        os.utime(p, (mtime, mtime))
    return p


def recs(root):
    return records.load_all(root)[0]


def by_path(root, rel):
    return next((s for s in recs(root).values() if s["path"] == rel and not s.get("missing")), None)


# ---------------------------------------------------------------------------
print("Test 1: first run over existing notes -- records made, notes untouched")
N = tempfile.mkdtemp(prefix="jarvis-notes-")
write(N, "captures/milk-2026-09-01.md", "# Buy milk\n\nCaptured 2026-09-01.\n\nbuy milk\n")
write(N, "meetings/2026-08-15-standup.md", "# Standup\n\nNotes.\n")
old = time.mktime((2025, 3, 4, 12, 0, 0, 0, 0, -1))
write(N, "ideas/japan.md", "# Japan\n\nI'd love to visit Japan.\n", mtime=old)
before = note_bytes(N)
report = records.sync_and_index(N)
check("three records created", sorted(report["created"]) == sorted(before))
check("no note changed by a byte", note_bytes(N) == before)
a = by_path(N, "captures/milk-2026-09-01.md")
b = by_path(N, "meetings/2026-08-15-standup.md")
c = by_path(N, "ideas/japan.md")
check("date written in the note is used, and labelled as such",
      a["created"] == "2026-09-01" and a["created_from"] == "date-in-note")
check("date in the file name is used next", b["created"] == "2026-08-15" and b["created_from"] == "date-in-filename")
check("otherwise the file's modified time, labelled as the weakest",
      c["created"].startswith("2025-03-04") and c["created_from"] == "file-modified-time")
check("existing notes: source 'file', no invented original input",
      all(s["source"] == "file" and s["original_input"] is None for s in (a, b, c)))
check("everything starts in INBOX, unprocessed, with no AI interpretations",
      all(s["home"] == "INBOX" and s["status"] == "unprocessed" and s["interpretations"] == [] for s in (a, b, c)))
check("the record's fingerprint matches the note", a["content_sha256"] == before["captures/milk-2026-09-01.md"])
check("ids are unique", len({s["id"] for s in recs(N).values()}) == 3)

print("\nTest 2: running it again changes nothing")
snap = {k: json.dumps(v, sort_keys=True) for k, v in recs(N).items()}
report = records.sync_and_index(N)
check("nothing reported", all(not v for v in report.values()))
check("records identical", {k: json.dumps(v, sort_keys=True) for k, v in recs(N).items()} == snap)

print("\nTest 3: a capture keeps your exact words, trigger phrase and all")
p = write(N, "captures/call-the-dentist-2026-09-22.md", "# Call the dentist\n\nCaptured 2026-09-22.\n\ncall the dentist\n")
said = "  Um, remember that call the dentist  "
sc = records.create_for_capture(p, said, source="spoken", notes_dir=N)
records.sync_and_index(N)
sc = by_path(N, "captures/call-the-dentist-2026-09-22.md")
check("original input stored exactly, spaces and all", sc["original_input"] == said)
check("source 'spoken', created at the capture moment",
      sc["source"] == "spoken" and sc["created_from"] == "capture-time" and "T" in sc["created"])
check("the sync didn't make a second record for it", sum(1 for s in recs(N).values() if s["path"] == sc["path"]) == 1)
check("an unknown source is stored as 'unknown', not trusted",
      records.create_for_capture(write(N, "captures/x.md", "# X\n"), "x", source="hacked", notes_dir=N)["source"] == "unknown")

print("\nTest 4: you edit a note outside Jarvis -> noticed, logged, not undone")
before_sha = by_path(N, "ideas/japan.md")["content_sha256"]
write(N, "ideas/japan.md", "# Japan\n\nI'd love to visit Japan. Maybe in spring.\n")
report = records.sync_and_index(N)
c = by_path(N, "ideas/japan.md")
check("reported as edited", report["edited"] == ["ideas/japan.md"])
check("your edit is left as you made it", "Maybe in spring" in open(records.abs_path("ideas/japan.md", N)).read())
check("history has the before/after fingerprints",
      c["history"][-1]["event"] == "edited outside Jarvis" and c["history"][-1]["detail"]["before_sha256"] == before_sha)
check("same record id kept", c["id"] == by_path(N, "ideas/japan.md")["id"])

print("\nTest 5: you move/rename a note outside Jarvis -> the record follows it")
rid = by_path(N, "meetings/2026-08-15-standup.md")["id"]
os.makedirs(records.abs_path("archive", N))
os.rename(records.abs_path("meetings/2026-08-15-standup.md", N), records.abs_path("archive/standup-old.md", N))
report = records.sync_and_index(N)
check("reported as moved", report["moved"] == ["archive/standup-old.md"])
moved = recs(N)[rid]
check("same record, new path, created date kept",
      moved["path"] == "archive/standup-old.md" and moved["created"] == "2026-08-15")
check("the move is in its history", moved["history"][-1]["event"] == "moved")

print("\nTest 6: a note disappears -> its record is kept and marked, never deleted")
rid = by_path(N, "captures/x.md")["id"]
tmp_hold = os.path.join(tempfile.mkdtemp(), "x.md")
shutil.move(records.abs_path("captures/x.md", N), tmp_hold)
report = records.sync_and_index(N)
check("reported missing", report["missing"] == ["captures/x.md"])
check("record still there, marked missing", recs(N)[rid]["missing"] is True)
report = records.sync_and_index(N)
check("not reported twice", report["missing"] == [])
shutil.move(tmp_hold, records.abs_path("captures/x.md", N))
report = records.sync_and_index(N)
check("it comes back -> restored, same record", report["restored"] == ["captures/x.md"] and not recs(N)[rid]["missing"])

print("\nTest 7: Jarvis appends a link at your request -> not mistaken for an outside edit")
p = records.abs_path("ideas/japan.md", N)
with open(p, "a", encoding="utf-8") as f:
    f.write("\nSee also: [[Travel]]\n")
records.record_append(p, "See also link", notes_dir=N, target="Travel")
records.record_link(p, "Travel", by="user", notes_dir=N)
report = records.sync_and_index(N)
c = by_path(N, "ideas/japan.md")
check("sync sees no outside edit", report["edited"] == [])
check("the link is recorded, confirmed because you chose it",
      c["links"][-1]["target_title"] == "Travel" and c["links"][-1]["confirmed"] is True)
check("the text above the link is exactly what it was",
      open(p).read().startswith("# Japan\n\nI'd love to visit Japan. Maybe in spring.\n"))

print("\nTest 8: filing a note under a folder -> the record moves with it")
p_old = records.abs_path("captures/call-the-dentist-2026-09-22.md", N)
rid = by_path(N, "captures/call-the-dentist-2026-09-22.md")["id"]
os.makedirs(records.abs_path("team", N), exist_ok=True)
p_new = records.abs_path("team/call-the-dentist-2026-09-22.md", N)
os.rename(p_old, p_new)
records.record_move(p_old, p_new, why="you filed it under team", notes_dir=N)
report = records.sync_and_index(N)
check("record path updated, nothing reported missing",
      recs(N)[rid]["path"] == "team/call-the-dentist-2026-09-22.md" and report["missing"] == [])
check("original words still there after the move", recs(N)[rid]["original_input"] == said)

print("\nTest 9: delete every record -> rebuilt from the notes, nothing lost")
before = note_bytes(N)
shutil.rmtree(records.store_dir(N))
report = records.sync_and_index(N)
check("a record for every note again", sorted(report["created"]) == sorted(before))
check("notes untouched", note_bytes(N) == before)
check("diag healthy", records.diag(N)["healthy"])

print("\nTest 10: the index -- search, and it's only a cache")
hits = records.search("Japan spring", notes_dir=N)
check("full-text search finds the note", [h[1] for h in hits] == ["ideas/japan.md"])
check("no match -> nothing", records.search("zebrafish", notes_dir=N) == [])
check("odd characters don't break it", records.search('"; DROP TABLE records; --', notes_dir=N) == [])
os.remove(records.index_path(N))
check("index deleted -> diag says so", records.diag(N)["index"] is None and not records.diag(N)["healthy"])
records.rebuild_index(N)
check("rebuilt -> search works again", records.search("Japan", notes_dir=N)[0][1] == "ideas/japan.md")

print("\nTest 11: no FTS5 in this Python -> plain search still works")
real = records._fts_available
records._fts_available = lambda: False
records.rebuild_index(N)
check("basic mode recorded", records.diag(N)["index"]["search"] == "basic")
check("basic search finds it", [h[1] for h in records.search("japan spring", notes_dir=N)] == ["ideas/japan.md"])
records._fts_available = real
records.rebuild_index(N)

print("\nTest 12: a damaged record file is reported, never deleted or replaced")
bad = os.path.join(records.sidecar_dir(N), "r-broken.json")
with open(bad, "w") as f:
    f.write("{not json")
d = records.diag(N)
check("reported in diag", d["unreadable_record_files"] == ["r-broken.json"] and not d["healthy"])
records.sync_and_index(N)
check("still there, untouched, after a sync", open(bad).read() == "{not json")
os.remove(bad)

print("\nTest 13: the notes/.jarvis folder is never mistaken for notes")
write(N, ".jarvis/sneaky.md", "# not a note\n")
check("skipped", ".jarvis/sneaky.md" not in records.note_files(N))

print("\nTest 14: an empty notes folder is fine")
E = tempfile.mkdtemp(prefix="jarvis-empty-")
check("sync on nothing reports nothing", all(not v for v in records.sync_and_index(E).values()))
d = records.diag(E)
check("diag: 0 notes, healthy", d["notes"] == 0 and d["healthy"])

# ---------------------------------------------------------------------------
print("\nTest 15: server.py with a throwaway notes folder -- the real code paths")
import server
W = tempfile.mkdtemp(prefix="jarvis-server-")
real_notes, real_graph, real_viewer = server.NOTES_DIR, server.GRAPH_DATA_PATH, server.VIEWER_DIR
server.NOTES_DIR = os.path.join(W, "notes")
os.makedirs(server.NOTES_DIR)
os.makedirs(os.path.join(W, "viewer"))
server.GRAPH_DATA_PATH = os.path.join(W, "viewer", "graph-data.js")
real_run = server.subprocess.run


def run_in_w(cmd, cwd=None, **kw):     # build.py writes viewer/graph-data.js relative to cwd
    return real_run(cmd, cwd=W, **kw)


server.subprocess.run = run_in_w
try:
    server.rebuild_graph()
    check("empty notes folder: the galaxy builds (empty) instead of failing",
          server.load_graph() == {"nodes": [], "links": []})
    said = "Remember that Priya prefers green tea"
    node, _n, conf, graph, _s, person = server.capture_new_note(
        server.extract_capture_content(said), original_input=said, source="typed")
    sc = records.find_by_path(node["path"], server.NOTES_DIR)
    check("the very first capture works on an empty folder", len(graph["nodes"]) == 1)
    check("its record holds your exact words", sc and sc["original_input"] == said and sc["source"] == "typed")
    check("the node carries its record id, no warning",
          node.get("record_id") == sc["id"] and "record_warning" not in node)
    body = open(node["path"]).read()
    check("the note itself is the same format as before", body.startswith("# Priya prefers green tea\n\nCaptured "))
    before = body
    server.apply_link(node["path"], "link_to", "Tea Notes")
    sc = records.find_by_path(node["path"], server.NOTES_DIR)
    check("link: text only appended to", open(node["path"]).read().startswith(before))
    check("link: recorded, and the sync didn't call it an outside edit",
          sc["links"][-1]["target_title"] == "Tea Notes"
          and all(h["event"] != "edited outside Jarvis" for h in sc["history"]))
    pnode, *_ = server.create_person_note("Priya", "team", node["path"])
    psc = records.find_by_path(pnode["path"], server.NOTES_DIR)
    check("person note gets a 'person-note' record", psc and psc["source"] == "person-note")
    check("and the source note's append is recorded", records.find_by_path(node["path"], server.NOTES_DIR)["links"][-1]["target_title"] == "Priya")
    rid = sc["id"]
    moved_node, *_ = server.apply_link(node["path"], "file_under", "clients")
    check("file under: the record follows the note",
          records.load_all(server.NOTES_DIR)[0][rid]["path"] == "clients/" + os.path.basename(node["path"]))
    d = records.diag(server.NOTES_DIR)
    check("after all that: healthy, 2 notes, 2 records", d["healthy"] and d["notes"] == 2 and d["records"] == 2)

    # A record failure must not lose the capture, and must not be silent.
    real_create = records.create_for_capture
    records.create_for_capture = lambda *a, **k: (_ for _ in ()).throw(OSError("disk says no"))
    node, *_ = server.capture_new_note("the boiler code is 4471", original_input="remember that the boiler code is 4471")
    records.create_for_capture = real_create
    check("record write fails -> the note is still saved", os.path.exists(node["path"]))
    check("...and the reply says so", "disk says no" in node.get("record_warning", ""))
    check("...and /diag's status has it", "disk says no" in server.RECORDS_STATUS["last_error"]["error"])
    check("...and the sync still gives it a (weaker) record", records.find_by_path(node["path"], server.NOTES_DIR)["source"] == "file")
finally:
    server.subprocess.run = real_run
    server.NOTES_DIR, server.GRAPH_DATA_PATH = real_notes, real_graph

print(f"\n{PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)
