"""
5.0.0 (brief 3 §3-10): one way in for everything -- files (TXT, MD, CSV,
DOCX, XLSX, PDF), clipboard, web pages/apps, the inbox state names, and
"where did you get that?". Throwaway folders; your real notes are never
touched. Run: python3 test_capture_layer.py
"""
import base64, io, json, os, re, shutil, subprocess, tempfile, threading, urllib.request, zipfile, zlib
from http.server import ThreadingHTTPServer
import capture, records, server

PASS = FAIL = 0
ROOT = os.path.dirname(os.path.abspath(__file__))


def check(label, ok):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
    print(f"  [{'OK' if ok else 'FAIL'}]{'   ' if ok else ' '}{label}")


def docx(paras):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        body = "".join(f'<w:p><w:r><w:t xml:space="preserve">{p}</w:t></w:r></w:p>' for p in paras)
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document xmlns:w="x"><w:body>{body}</w:body></w:document>')
    return b.getvalue()


def xlsx():
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("xl/sharedStrings.xml", '<sst><si><t>Item</t></si><si><t>Cost</t></si><si><t>Tyres</t></si></sst>')
        z.writestr("xl/worksheets/sheet1.xml", '<worksheet><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
                   '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>240</v></c></row>'
                   '<row r="3"><c r="A3" t="inlineStr"><is><t>Service &amp; MOT</t></is></c><c r="B3"><v>95.5</v></c></row></sheetData></worksheet>')
    return b.getvalue()


def pdf(content, flate=True):
    stream = zlib.compress(content) if flate else content
    filt = b"/Filter /FlateDecode " if flate else b""
    return (b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\n4 0 obj << " + filt + b"/Length "
            + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream\nendobj\n%%EOF")


print("Test 1: reading files (standard library only)")
t, k, q = capture.extract("notes.txt", "Café opening hours: 9-5\n".encode("utf-8"))
check("TXT (UTF-8)", t == "Café opening hours: 9-5" and q == "full")
t, k, q = capture.extract("old.txt", "caf\xe9 list".encode("cp1252"))
check("TXT (an older Windows encoding)", t == "café list")
t, k, q = capture.extract("costs.csv", b"item,cost\ntyres,240\n")
check("CSV", "tyres,240" in t and k == "csv")
t, k, q = capture.extract("letter.docx", docx(["Dear Liam,", "The quote for the van is &#163;240.", "Regards, Dave"]))
check("DOCX: paragraphs, in order, entities decoded", t.split("\n")[:3] == ["Dear Liam,", "The quote for the van is £240.", "Regards, Dave"] and q == "full")
t, k, q = capture.extract("costs.xlsx", xlsx())
check("XLSX: shared and inline strings, numbers, one row per line", "Item\tCost" in t and "Tyres\t240" in t and "Service & MOT\t95.5" in t)
t, k, q = capture.extract("invoice.pdf", pdf(b"BT /F1 12 Tf 72 700 Td (Invoice 2026-0412 for van tyres) Tj T* [(Total ) -250 (due: \\243240)] TJ ET"))
check("PDF (compressed): the text is pulled out, marked partial", "Invoice 2026-0412 for van tyres" in t and "Total" in t and "£240" in t and q == "partial")
t, k, q = capture.extract("plain.pdf", pdf(b"BT (Uncompressed text here in a simple PDF file) Tj ET", flate=False))
check("PDF (uncompressed)", "Uncompressed text here" in t)
t, k, q = capture.extract("scan.pdf", pdf(b"q 100 0 0 100 0 0 cm /Im1 Do Q"))
check("a scanned PDF with no text: says 'none', doesn't invent", t == "" and q == "none")
check("a picture goes to the photo reader", capture.extract("shot.png", b"\x89PNG")[1] == "image")
try:
    capture.extract("thing.exe", b"MZ"); ok = False
except ValueError as e:
    ok = "can't read" in str(e)
check("anything else is refused, saying what works", ok)

print("Test 2: a file becomes ONE note, the original kept byte for byte")
N = tempfile.mkdtemp(prefix="jarvis-caplayer-")
data = docx(["Worcester stock report", "Zack wants the paperwork done by Friday."])
sc, note, info = capture.save_file("Worcester Report.docx", data, N)
kept = os.path.join(N, sc["source_file"])
check("original kept byte for byte in notes/sources", open(kept, "rb").read() == data and "sources" in sc["source_file"])
check("one note stands for it, saying where the original is", open(note).read().startswith("# Worcester Report")
      and os.path.basename(kept) in open(note).read() and "Zack wants the paperwork done by Friday." in open(note).read())
check("its record: source file, fingerprint, capture details, in the inbox", sc["source"] == "file" and sc["source_file_sha256"]
      and sc["capture"]["filename"] == "Worcester Report.docx" and sc["capture"]["kind"] == "docx" and sc["home"] == "INBOX")
check("state: RAW until sorted", capture.state_of(sc) == "RAW")
big = ("word " * 20000).encode()
sc2, note2, info2 = capture.save_file("huge.txt", big, N)
check("a long file: the note holds the first part and says so; the original has it all",
      info2["truncated"] and "the rest is in the original" in open(note2).read() and os.path.getsize(os.path.join(N, sc2["source_file"])) == len(big))
before = sorted(os.listdir(os.path.join(N, "sources")))
for bad_name, bad_data in (("x.exe", b"MZ"), ("empty.txt", b""), ("big.txt", b"x" * (capture.MAX_FILE_BYTES + 1))):
    try:
        capture.save_file(bad_name, bad_data, N); ok = False
    except ValueError:
        ok = True
    check(f"refused, nothing written: {bad_name}", ok and sorted(os.listdir(os.path.join(N, "sources"))) == before)

print("Test 3: states and 'where did you get that?'")
check("PROCESSED once sorted; NEEDS CONFIRMATION when Jarvis wants your word",
      capture.state_of({"status": "sorted"}) == "PROCESSED" and capture.state_of({"status": "needs_clarification"}) == "NEEDS CONFIRMATION")
src = capture.source_of(sc, N)
check("a file: named, dated, where the original is", "imported from a file" in src and "Worcester Report.docx" in src and "notes/sources" in src.replace(os.sep, "/"))

print("Test 4: over HTTP -- file drop, clipboard, web page, provenance, inbox")
W = tempfile.mkdtemp(prefix="jarvis-caplayer-w-")
real = {k: getattr(server, k) for k in ("NOTES_DIR", "GRAPH_DATA_PATH", "call_brain", "load_config")}
real_run = server.subprocess.run
server.NOTES_DIR, server.GRAPH_DATA_PATH = N, os.path.join(W, "viewer", "graph-data.js")
os.makedirs(os.path.join(W, "viewer"))
server.subprocess.run = lambda cmd, cwd=None, **kw: real_run(cmd, cwd=W, **kw)
server.rebuild_graph()
server.call_brain = lambda config, messages, **kw: ("The paperwork is due Friday, sir.", "stand-in/model", None)
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
    d = post("/capture/file", {"filename": "costs.xlsx", "data_base64": base64.b64encode(xlsx()).decode()})
    check("/capture/file: filed, the galaxy updated, said plainly", d["ok"] and "costs.xlsx" in d["spoken"] and d["graph"]["nodes"])
    d = post("/capture/file", {"filename": "scan.pdf", "data_base64": base64.b64encode(pdf(b"q /Im1 Do Q")).decode()})
    check("a scanned PDF: kept, and it says it couldn't read text", d["ok"] and "couldn't read any text" in d["spoken"])
    check("/capture/file refuses what it can't read", not post("/capture/file", {"filename": "a.exe", "data_base64": "TVo="})["ok"])
    inbox_items = get("/inbox")["items"]
    x = next(i for i in inbox_items if i.get("file") == "costs.xlsx")
    check("inbox: the imported file shows as a file (not 'photo kept'), state RAW", x["state"] == "RAW" and x["photo"] is None)
    clip = "remember to renew the van tax -- copied from the DVLA page"
    d = post("/remember", {"message": clip, "source": "clipboard", "mode": "paste"})
    rec = records.load_all(N)[0][d["node"]["record_id"]]
    check("clipboard: kept word for word (even starting with 'remember'), marked as from the clipboard",
          clip in open(os.path.join(N, rec["path"])).read() and rec["capture"]["source"] == "clipboard")
    d = post("/remember", {"message": "Tyres: 205/55 R16", "source": "browser", "mode": "paste",
                           "url": "https://example.com/tyres", "title": "Tyre sizes"})
    rec = records.load_all(N)[0][d["node"]["record_id"]]
    check("a web page: the address and title are kept with it (ready for a browser button)",
          rec["capture"]["url"] == "https://example.com/tyres" and "Tyre sizes" in capture.source_of(rec, N))
    post("/session/new", {})
    post("/chat", {"message": "when does Zack want the Worcester paperwork?"})
    a = post("/ask", {"question": "Where did you get that?"})
    check("'where did you get that?': names the file it came from", a["intent"] == "provenance" and "Worcester Report.docx" in a["spoken"])
    post("/session/new", {})
    post("/chat", {"message": "what's the capital of France?"})
    a = post("/ask", {"question": "where did you get that from"})
    check("...and says plainly when none of your notes were used", "general knowledge" in a["spoken"])
finally:
    httpd.shutdown()
    for k, v in real.items():
        setattr(server, k, v)
    server.subprocess.run = real_run

print("Test 5: the page's clipboard phrases (its own JavaScript, run in Node)")
page = open(os.path.join(ROOT, "viewer", "index.html"), encoding="utf-8").read()
js = "\n".join(re.search(rf"const {n} = (/.*?/i);", page).group(0) for n in ("CLIP_SAVE_RE", "CLIP_ASK_RE"))
cases = {"remember this": "save", "save my clipboard": "save", "capture the clipboard": "save", "Remember what's on my clipboard": "save",
         "what is this": "ask", "what's this": "ask", "explain this": "ask",
         "remember that": None, "remember to call Dave": None, "what is this project about": None}
out = subprocess.run(["node", "-e", js + "\nconst c=" + json.dumps(list(cases)) +
                      ";console.log(JSON.stringify(c.map(t=>CLIP_SAVE_RE.test(t)?'save':CLIP_ASK_RE.test(t)?'ask':null)))"],
                     capture_output=True, text=True)
got = dict(zip(cases, json.loads(out.stdout)))
for t, want in cases.items():
    check(f"'{t}' -> {want}", got[t] == want)
print("Test 6: sorting sends only what's needed")
import sorting
from testkit import StandIn, reading
S = tempfile.mkdtemp(prefix="jarvis-caplayer-sort-")
capture.save_file("long.txt", ("start of the file. " + "filler words here. " * 3000 + "wifi password: Hunter22").encode(), S)
brain_seen = StandIn({"start of the file": reading()})
sorting.sort_inbox(brain_seen, {}, notes_dir=S)
sent = json.dumps(brain_seen.last_payload)
check("a long file goes out as its opening part, marked as such", len(sent) < 6000 and "isn't shown" in sent)
capture.save_file("wifi.txt", b"Router details\nwifi password: Hunter22", S)
brain_seen = StandIn({"Router": reading()})
sorting.sort_inbox(brain_seen, {}, notes_dir=S)
check("a password in a note is hidden before sorting", "Hunter22" not in json.dumps(brain_seen.last_payload))
shutil.rmtree(S, ignore_errors=True)
shutil.rmtree(N, ignore_errors=True); shutil.rmtree(W, ignore_errors=True)
print(f"\n{PASS} passed, {FAIL} failed")
