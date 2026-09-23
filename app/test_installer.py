"""
4.3.0: the installer -- copying the program only, updates that never touch
your notes, bringing notes across, the Mac app, uninstalling. Runs in
throwaway folders (the Windows-only icon and Apps-entry steps can't run
here; they're checked on Windows by hand). Run: python3 test_installer.py
"""
import json, os, plistlib, shutil, stat, sys, tempfile
import installer, make_share

PASS = FAIL = 0
def check(label, ok):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
    print(f"  [{'OK' if ok else 'FAIL'}]{'   ' if ok else ' '}{label}")

installer.IS_WINDOWS = installer.IS_MAC = False
installer.stop_running = lambda: False
T = tempfile.mkdtemp(prefix="jarvis-inst-")
SRC = os.path.join(T, "unzipped"); DEST = os.path.join(T, "installed")
make_share.main(SRC)                                    # what a friend downloads
APP = os.path.join(SRC, "app")                          # 4.4.0: the program sits in app/

print("Test 1: a fresh install")
installer.install(src=APP, dest=DEST, ask=False, start=False)
have = {os.path.relpath(os.path.join(r, f), DEST).replace(os.sep, "/") for r, _, fs in os.walk(DEST) for f in fs}
check("the program is there (server, page, boot screen, launcher, icons, installer)",
      {"server.py", "viewer/index.html", "viewer/boot.html", "jarvis_launcher.pyw", "assets/jarvis.ico", "installer.py", "make_share.py"} <= have)
check("nothing personal was installed", not any(h.startswith(("notes/", "usage/", "logs/")) for h in have))
cfg = json.load(open(os.path.join(DEST, "config.json"))) if "config.json" in have else {}
check("config.json only holds the browser choice (no key)", set(cfg) <= {"app_browser"} and cfg.get("app_browser", "auto") == "auto")

print("Test 2: using it, then updating")
os.makedirs(os.path.join(DEST, "notes", "captures"))
open(os.path.join(DEST, "notes", "captures", "mine.md"), "w").write("# Mine\n\nmy note\n")
json.dump({"openrouter_api_key": "sk-or-v1-" + "ab" * 32}, open(os.path.join(DEST, "config.json"), "w"))
open(os.path.join(APP, "server.py"), "a").write("\n# newer version\n")
installer.install(src=APP, dest=DEST, ask=False, start=False)
check("the update replaced the program", open(os.path.join(DEST, "server.py")).read().endswith("# newer version\n"))
check("your notes and key are untouched", open(os.path.join(DEST, "notes", "captures", "mine.md")).read() == "# Mine\n\nmy note\n"
      and "ab" * 32 in open(os.path.join(DEST, "config.json")).read())

print("Test 3: someone who used Jarvis from the unzipped folder first")
S2 = os.path.join(T, "old-unzip"); shutil.copytree(APP, S2)
os.makedirs(os.path.join(S2, "notes")); open(os.path.join(S2, "notes", "idea.md"), "w").write("# Idea\n")
json.dump({"openrouter_api_key": "sk-or-v1-" + "cd" * 32, "address": "name", "user_name": "Dad"}, open(os.path.join(S2, "config.json"), "w"))
D2 = os.path.join(T, "fresh")
check("finds their notes and key", installer.found_user_data(S2, D2) == ["notes", "config.json"])
installer.install(src=S2, dest=D2, ask=False, start=False)
check("brought across into the install", os.path.exists(os.path.join(D2, "notes", "idea.md"))
      and json.load(open(os.path.join(D2, "config.json")))["user_name"] == "Dad")
check("…and left in the old folder too (copied, not moved)", os.path.exists(os.path.join(S2, "notes", "idea.md")))
json.dump({"openrouter_api_key": "PUT-YOUR-KEY-HERE"}, open(os.path.join(S2, "config.json"), "w"))
check("a placeholder key isn't worth bringing", "config.json" not in installer.found_user_data(S2, os.path.join(T, "x")))
check("never merged into notes that are already installed", installer.found_user_data(S2, D2) == [])

print("Test 4: the Mac app")
apps = os.path.join(T, "Applications")
app = installer.mac_app(DEST, apps_dir=apps)
plist = plistlib.load(open(os.path.join(app, "Contents", "Info.plist"), "rb"))
exe = os.path.join(app, "Contents", "MacOS", "Jarvis")
check("Jarvis.app: a proper bundle with name, version and icon", plist["CFBundleName"] == "Jarvis" and plist["CFBundleExecutable"] == "Jarvis"
      and plist["CFBundleIconFile"] == "jarvis" and os.path.exists(os.path.join(app, "Contents", "Resources", "jarvis.icns")))
check("it runs the launcher from the install folder, and is runnable", os.stat(exe).st_mode & stat.S_IXUSR
      and os.path.join(DEST, "jarvis_launcher.pyw") in open(exe).read())
installer.mac_app(DEST, apps_dir=apps)
check("installing again replaces the app cleanly", os.path.exists(exe))

print("Test 5: uninstall keeps your notes")
installer.uninstall(dest=DEST, ask=False)
left = {os.path.relpath(os.path.join(r, f), DEST).replace(os.sep, "/") for r, _, fs in os.walk(DEST) for f in fs}
check("the program is gone", not any(x.endswith(".py") or x.endswith(".pyw") or x.startswith("viewer/") for x in left))
check("your notes and key are still there", "notes/captures/mine.md" in left and "config.json" in left)

print("Test 6: Setup.exe script sanity")
iss = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "installer", "jarvis.iss")).read()
check("per-user install, no admin prompt", "PrivilegesRequired=lowest" in iss and r"{localappdata}\Programs\Jarvis" in iss)
check("never packs personal files", all(x in iss for x in (r"\notes\*", r"\config.json", r"\usage\*", r"\logs\*", r"\focus_ledger.json")))
check("versions agree", '#define AppVersion "' + installer.VERSION + '"' in iss)
check("4.4.0: Desktop icon is a choice, off unless ticked", 'Name: "desktopicon"' in iss and "Flags: unchecked" in iss.split('Name: "desktopicon"')[1].split("\n")[0])
check("4.4.0: no console windows (pythonw, hidden steps, no .bat)", ".bat" not in iss.split("[Run]")[1].split("[UninstallRun]")[0]
      and "runhidden" in iss.split("[Run]")[1] and "{code:PythonW}" in iss)
check("4.4.0: a browser page and a keep-your-notes question", "CreateInputOptionPage" in iss and "MB_DEFBUTTON2" in iss)
check("4.4.0: private Python stays private (not on PATH, no launcher)", "PrependPath=0" in iss and "Include_launcher=0" in iss)
wf = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "installer", "build-installer.yml")).read()
check("4.4.0: GitHub build checks Python's signature and publishes Jarvis-Setup.exe",
      "Get-AuthenticodeSignature" in wf and "Jarvis-Setup.exe" in wf and "contents: write" in wf)
top = set(os.listdir(SRC)) - {".git"}
check("4.4.0: a download shows only README, LICENSE, installers and app/", top == {"README.md", "LICENSE", ".gitignore", ".gitattributes",
      "Install Jarvis (Mac).command", "Install Jarvis (Windows).bat", "installer", ".github", "app"})
shutil.rmtree(T, ignore_errors=True)
print(f"\n{PASS} passed, {FAIL} failed")
