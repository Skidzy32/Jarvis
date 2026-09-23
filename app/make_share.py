"""
make_share.py — 4.4.0: build a clean copy of Jarvis to share.

    python make_share.py                  -> ..\\jarvis-share
    python make_share.py D:\\somewhere\\jarvis

Copies ONLY the program: the code, the viewer page, the demo notes
(examples/), the starters and the README. It never copies your notes,
config.json (your key), usage data, focus history, logs or review
settings -- they aren't on the list, and after copying, the new folder is
scanned for anything that looks like a key or a personal file. If the scan
finds something, the copy is deleted and it says what it found.

4.4.0 layout, so a visitor sees only what matters:

    README.md, LICENSE                 what it is, how to install
    Install Jarvis (Mac).command       the Mac installer
    Install Jarvis (Windows).bat       a backup for Windows (Setup.exe is the main way)
    installer/jarvis.iss               recipe for Jarvis-Setup.exe
    .github/workflows/                 GitHub builds Jarvis-Setup.exe from that recipe
    app/                               the program itself

and it's tagged v<VERSION>, so `git push --follow-tags` makes GitHub build
Jarvis-Setup.exe and publish it under Releases.

The new folder has no git history, so nothing from your own history comes
with it. Standard library only.
"""

import os
import re
import shutil
import subprocess
import sys

VERSION = "4.4.0"
GITATTRIBUTES = """# Line endings that work on each system, whatever git settings the uploader has.
*.bat      text eol=crlf
*.command  text eol=lf
*.py       text eol=lf
*.md       text eol=lf
*.html     text eol=lf
*.pyw      text eol=lf
*.iss      text eol=crlf
*.yml      text eol=lf
*.png      binary
*.ico      binary
*.icns     binary
*.bmp      binary
"""
MAC_STARTER = "Start Jarvis (Mac).command"

HERE = os.path.dirname(os.path.abspath(__file__))
SHARE_FILES = ["Start Jarvis.bat", "Stop Jarvis.bat", "Start Jarvis (Mac).command", "README.md", "LICENSE",
               ".gitignore", os.path.join("viewer", "index.html"), os.path.join("viewer", "boot.html"),
               "jarvis_launcher.pyw", "Install Jarvis.bat", "Install Jarvis (Mac).command",
               os.path.join("installer", "jarvis.iss"), os.path.join("installer", "build-installer.yml"),
               os.path.join("installer", "wizard-large.bmp"), os.path.join("installer", "wizard-small.bmp")]
SHARE_DIRS = ["examples", "browser_launchers", "assets", os.path.join("viewer", "assets")]
# 4.4.0: these sit at the top of the shared copy; everything else goes in app/.
APP_DIR = "app"
TOP_LEVEL = {"README.md", "LICENSE", ".gitignore", "Install Jarvis (Mac).command",
             os.path.join("installer", "jarvis.iss"),
             os.path.join("installer", "wizard-large.bmp"), os.path.join("installer", "wizard-small.bmp")}
TOP_RENAME = {"Install Jarvis.bat": "Install Jarvis (Windows).bat",     # copied to the top, under these names
              # kept in installer/ here (tools can't write into .github); GitHub needs it in .github/workflows
              os.path.join("installer", "build-installer.yml"): os.path.join(".github", "workflows", "build-installer.yml")}
MAC_COMMANDS = ["Install Jarvis (Mac).command", "app/Start Jarvis (Mac).command"]
NEVER = {"config.json", "focus_ledger.json", "review_settings.json", "graph-data.js", "server.log", "overlay_position.json"}
NEVER_DIRS = {"notes", "usage", "logs", "__pycache__", ".git", "jarvis-profiles"}
KEY_RE = re.compile(r"sk-or-v1-[0-9a-f]{20,}|sk-[A-Za-z0-9]{32,}")


def files_to_share():
    out = [f for f in sorted(os.listdir(HERE)) if f.endswith(".py") and os.path.isfile(os.path.join(HERE, f))]
    out += [f for f in SHARE_FILES if os.path.isfile(os.path.join(HERE, f))]
    for d in SHARE_DIRS:
        for root, dirs, files in os.walk(os.path.join(HERE, d)):
            dirs[:] = [x for x in dirs if x not in NEVER_DIRS]
            out += [os.path.relpath(os.path.join(root, f), HERE) for f in files]
    return out


def shared_places(rel):
    """Where a program file goes in the shared copy (4.4.0): top level or app/."""
    if rel in TOP_LEVEL:
        return [rel]
    if rel in TOP_RENAME:
        return [TOP_RENAME[rel]]
    return [os.path.join(APP_DIR, rel)]


def scan(folder):
    """Anything that shouldn't be in a shared copy."""
    problems = []
    for root, dirs, files in os.walk(folder):
        if root == folder:
            # 4.4.0: the shared copy's own git history is expected (updating an
            # existing jarvis-share); it isn't part of the copy being checked.
            dirs[:] = [d for d in dirs if d != ".git"]
        for d in dirs:
            if d in NEVER_DIRS:
                problems.append(f"folder {os.path.relpath(os.path.join(root, d), folder)}")
        for f in files:
            p = os.path.join(root, f)
            rel = os.path.relpath(p, folder)
            if f in NEVER:
                problems.append(f"personal file {rel}")
                continue
            try:
                with open(p, encoding="utf-8", errors="ignore") as fh:
                    if KEY_RE.search(fh.read()):
                        problems.append(f"something that looks like an API key in {rel}")
            except OSError:
                pass
    return problems


def main(dest=None):
    dest = os.path.abspath(dest or os.path.join(HERE, "..", "jarvis-share"))
    if os.path.commonpath([dest, HERE]) == HERE:
        sys.exit("Pick a folder outside the Jarvis folder.")
    if os.path.exists(dest) and os.listdir(dest):
        if os.path.isdir(os.path.join(dest, ".git")):
            # Updating an existing shared copy: replace the program files, keep its git history.
            for name in os.listdir(dest):
                if name != ".git":
                    p = os.path.join(dest, name)
                    shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
        else:
            sys.exit(f"{dest} already has files in it. Pick an empty or new folder.")
    files = files_to_share()
    for rel in files:
        for target in shared_places(rel):
            target = os.path.join(dest, target)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(os.path.join(HERE, rel), target)
    problems = scan(dest)
    if problems:
        for name in os.listdir(dest):
            if name != ".git":
                p = os.path.join(dest, name)
                shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
        print("STOPPED -- the copy was removed because it contained:")
        for p in problems:
            print("  -", p)
        return 1
    with open(os.path.join(dest, ".gitattributes"), "w", encoding="utf-8", newline="\n") as f:
        f.write(GITATTRIBUTES)
    git_ready = make_repo(dest)
    print(f"Clean copy made: {dest}")
    print("  Layout: README, LICENSE, the two installers, and everything else tidied into app/.")
    print(f"  {len(files)} files. Not included: your notes, config.json (your key), usage data, logs, history.")
    print("  Checked: no personal files and nothing that looks like a key.")
    if git_ready:
        print(f"  Git: saved as '{git_ready}' in a fresh repository (Mac starter marked runnable).")
    else:
        print("  Git wasn't found, so the repository step was skipped.")
    return 0


def make_repo(dest):
    """Fresh git repository (or a new commit in an existing shared one), with
    the Mac starter marked as runnable -- Windows can't record that itself."""
    def git(*args):
        return subprocess.run(["git", *args], cwd=dest, capture_output=True, text=True)
    try:
        if git("--version").returncode != 0:
            return None
    except OSError:
        return None
    if not os.path.isdir(os.path.join(dest, ".git")):
        if git("init", "-b", "main").returncode != 0 and git("init").returncode != 0:
            return None
    git("add", "-A")
    for cmd in MAC_COMMANDS:
        if os.path.exists(os.path.join(dest, cmd)):
            git("update-index", "--chmod=+x", cmd)
    msg = f"Jarvis {VERSION}"
    r = git("commit", "-m", msg)
    if r.returncode != 0 and "nothing to commit" not in (r.stdout + r.stderr):
        print("  (git commit didn't work: " + (r.stderr or r.stdout).strip()[:200] + ")")
        return None
    # 4.4.0: a version tag -- pushing it makes GitHub build Jarvis-Setup.exe.
    tag = f"v{VERSION}"
    if git("rev-parse", "-q", "--verify", f"refs/tags/{tag}").returncode != 0:
        git("tag", "-a", tag, "-m", msg)
    return msg + f" (tagged {tag})"


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
