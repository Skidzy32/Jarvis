"""
4.0.0: sharing Jarvis -- first-run settings, the form of address, sample
notes, the refusal of other websites, and the clean shareable copy.
Throwaway folders only. Run: python3 test_setup.py
"""
import json, os, shutil, subprocess, sys, tempfile, threading, urllib.request, urllib.error
import inbox, make_share, setup_flow

PASS = FAIL = 0
def check(label, ok):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
    print(f"  [{'OK' if ok else 'FAIL'}]{'   ' if ok else ' '}{label}")

T = tempfile.mkdtemp(prefix="jarvis-setup-")
cfg = os.path.join(T, "config.json"); notes = os.path.join(T, "notes"); os.makedirs(notes)
json.dump({"openrouter_api_key": "PUT-YOUR-KEY-HERE", "model_chain": ["openrouter/free"]}, open(cfg, "w"))
KEY = "sk-or-v1-" + "ab" * 32

print("Test 1: first run and saving")
st = setup_flow.state(cfg, "PUT-YOUR-KEY-HERE", notes)
check("placeholder key -> first run", st["first_run"] and not st["has_key"] and st["address"] == "sir")
setup_flow.save(cfg, {"openrouter_api_key": KEY, "address": "name", "user_name": "  Dad  ", "model_chain": ["evil/model"]})
c = json.load(open(cfg))
check("key, address and name saved; name tidied", c["openrouter_api_key"] == KEY and c["address"] == "name" and c["user_name"] == "Dad")
check("nothing else can be written through setup (model_chain kept)", c["model_chain"] == ["openrouter/free"])
st = setup_flow.state(cfg, "PUT-YOUR-KEY-HERE", notes)
check("state never contains the key", KEY not in json.dumps(st) and st["has_key"] and not st["first_run"])
try:
    setup_flow.save(cfg, {"address": "name", "user_name": ""}); ok = False
except ValueError: ok = True
check("'by my name' with no name is refused", ok)
try:
    setup_flow.save(cfg, {"address": "your majesty"}); ok = False
except ValueError: ok = True
check("an unknown form of address is refused", ok)
check("a blank key never wipes the saved one", setup_flow.save(cfg, {"openrouter_api_key": "  "})["openrouter_api_key"] == KEY)
check("a key that isn't an OpenRouter key is refused before any call", setup_flow.test_key("hello")[0] is False)

print("Test 2: what Jarvis calls you")
f = setup_flow.fix_address
check("name", f("Good evening, sir. 3 notes indexed.", "name", "Dad") == "Good evening, Dad. 3 notes indexed.")
check("madam", f("Sir, Instagram can wait.", "madam") == "Madam, Instagram can wait.")
check("nothing", f("Linked 'A' and 'B', sir.", "none") == "Linked 'A' and 'B'." and f("Sir, Instagram can wait.", "none") == "Instagram can wait.")
check("'Sir Alex' (a title) is left alone", f("Call Sir Alex, sir.", "none") == "Call Sir Alex." and "Sir Alex" in f("Call Sir Alex.", "name", "Dad"))
check("words containing 'sir' untouched", f("I desire nothing, sir.", "none") == "I desire nothing.")
p = setup_flow.fix_payload({"spoken": "Yes, sir.", "text": "# Note about sir walter, sir", "announcements": ["Back, sir."]}, "name", "Dad")
check("applied to Jarvis's lines only, never note text", p["spoken"] == "Yes, Dad." and p["text"] == "# Note about sir walter, sir"
      and p["announcements"] == ["Back, Dad."])
check("'sir' mode changes nothing", setup_flow.fix_payload({"spoken": "Yes, sir."}, "sir") == {"spoken": "Yes, sir."})

print("Test 3: sample notes")
check("added", setup_flow.add_samples(notes).startswith("Sample notes added") and os.path.isdir(os.path.join(notes, "samples")))
open(os.path.join(notes, "mine.md"), "w").write("# Mine\n\nmy own note\n")
check("removed to the bin; your own notes untouched", setup_flow.remove_samples(notes).startswith("Sample notes removed")
      and not os.path.isdir(os.path.join(notes, "samples")) and os.path.exists(os.path.join(notes, "mine.md"))
      and any(n.startswith("samples-") for n in os.listdir(os.path.join(notes, ".jarvis", "bin"))))

print("Test 4: the owner's own follow-ups aren't planted in other people's copies")
N2 = tempfile.mkdtemp()
check("server-style seeding adds nothing", inbox.seed_followups(N2, personal=False) == [] and not os.listdir(N2))

print("Test 5: other websites can't give Jarvis orders")
sys.argv = ["server.py"]
import server
from http.server import ThreadingHTTPServer
httpd = ThreadingHTTPServer(("localhost", 0), server.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
port = httpd.server_port
server.PORT = port
def post(path, origin=None):
    h = {"Content-Type": "text/plain"}
    if origin: h["Origin"] = origin
    req = urllib.request.Request(f"http://localhost:{port}{path}", data=b'{"key": "x"}', headers=h, method="POST")
    try:
        with urllib.request.urlopen(req) as r: return r.status
    except urllib.error.HTTPError as e: return e.code
check("from another website: refused (403)", post("/setup/save", "https://evil.example") == 403
      and post("/stars", "http://localhost.evil.example") == 403)
check("from Jarvis's own page: allowed", post("/setup/test", f"http://localhost:{port}") == 200)
httpd.shutdown()

print("Test 6: the shareable copy")
D = os.path.join(tempfile.mkdtemp(), "share")
rc = make_share.main(D)
have = set()
for root, dirs, files in os.walk(D):
    dirs[:] = [d for d in dirs if d != ".git"]
    have |= {os.path.relpath(os.path.join(root, x), D).replace(os.sep, "/") for x in files}
check("made", rc == 0 and "app/server.py" in have and "app/viewer/index.html" in have and "README.md" in have and "LICENSE" in have)
top = {h for h in have if "/" not in h}
check("4.4.0: the top level is tidy (README, LICENSE, the two installers)", top == {"README.md", "LICENSE", ".gitignore", ".gitattributes",
      "Install Jarvis (Mac).command", "Install Jarvis (Windows).bat"})
check("4.4.0: Setup.exe recipe and GitHub build are there", {"installer/jarvis.iss", "installer/wizard-large.bmp",
      ".github/workflows/build-installer.yml"} <= have)
check("the starters and the demo notes are in app/", {"app/Start Jarvis.bat", "app/Start Jarvis (Mac).command"} <= have
      and any(h.startswith("app/examples/") for h in have))
check("none of your things are", not any(h.startswith(("notes/", "usage/", "logs/")) or h.split("/")[-1] in make_share.NEVER for h in have))
if shutil.which("git"):
    mode = subprocess.run(["git", "ls-files", "-s", "Install Jarvis (Mac).command"], cwd=D, capture_output=True, text=True).stdout
    check("git: fresh repository, Mac installer marked runnable", mode.startswith("100755"))
    tags = subprocess.run(["git", "tag"], cwd=D, capture_output=True, text=True).stdout.split()
    check("git: tagged v" + make_share.VERSION + " (pushing it builds Setup.exe)", "v" + make_share.VERSION in tags)
    rc2 = make_share.main(D)                       # 4.4.0: updating an existing shared copy (it has .git)
    log = subprocess.run(["git", "log", "--oneline"], cwd=D, capture_output=True, text=True).stdout
    check("updating a shared copy that has git history works (and keeps it)", rc2 == 0 and os.path.exists(os.path.join(D, "app", "server.py"))
          and len(log.strip().splitlines()) >= 1)
planted = os.path.join(tempfile.mkdtemp(), "leaky"); os.makedirs(planted)
open(os.path.join(planted, "x.py"), "w").write('KEY = "sk-or-v1-' + "cd" * 32 + '"\n')
open(os.path.join(planted, "config.json"), "w").write("{}")
probs = make_share.scan(planted)
check("the scan catches a planted key and a config.json", any("API key" in p for p in probs) and any("config.json" in p for p in probs))
try:
    make_share.main(os.path.join(make_share.HERE, "inside")); ok = False
except SystemExit: ok = True
check("refuses a destination inside the Jarvis folder", ok and not os.path.exists(os.path.join(make_share.HERE, "inside")))

shutil.rmtree(T, ignore_errors=True)
print(f"\n{PASS} passed, {FAIL} failed")
