"""
installer.py — 4.3.0: install Jarvis properly, once.

Run by "Install Jarvis.bat" (Windows) or "Install Jarvis (Mac).command" --
people don't run this directly. It:

  Windows
    - installs to %LOCALAPPDATA%\\Programs\\Jarvis (for you only: no admin
      rights needed, the same place apps like VS Code use);
    - adds a Jarvis icon to the Desktop and the Start menu;
    - adds Jarvis to Settings > Apps, with an Uninstall button.
  Mac
    - installs to ~/Library/Application Support/Jarvis;
    - adds Jarvis.app to your Applications folder (~/Applications), so it's
      in Launchpad and Spotlight.

  Both
    - copies the PROGRAM only (the same allow-list as make_share.py);
    - running it again UPDATES the program and never touches your notes,
      config.json (your key), usage data, logs or history;
    - if the folder you're installing from already has notes or a key
      (someone who ran Jarvis from the unzipped folder), it offers to bring
      them in;
    - starts Jarvis at the end.

    python installer.py               install / update from this folder
    python installer.py --uninstall   remove Jarvis (asks about your notes)
    python installer.py --in-place    used by Setup.exe: the files are already
                                      in place; just add the icons

Standard library only.
"""

import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import make_share  # noqa: E402  (the program allow-list lives there)

VERSION = make_share.VERSION
IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
# Yours, never overwritten or removed by an update.
USER_DATA = ["notes", "config.json", "usage", "logs", "focus_ledger.json", "review_settings.json",
             "overlay_position.json", os.path.join("browser_launchers", "jarvis-profiles")]
BRING_ALONG = ["notes", "config.json", "usage", "focus_ledger.json", "review_settings.json"]
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Jarvis"


def install_dir():
    if IS_WINDOWS:
        return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Programs", "Jarvis")
    if IS_MAC:
        return os.path.expanduser("~/Library/Application Support/Jarvis")
    return os.path.expanduser("~/.local/share/jarvis")


def pythonw():
    """The windowless Python next to this one (Windows), else this Python."""
    if IS_WINDOWS:
        w = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if os.path.exists(w):
            return w
    return sys.executable


def copy_program(src, dest, files=None):
    """Copies the allow-listed program files; user data is never on the list."""
    files = files if files is not None else make_share.files_to_share()
    copied = 0
    for rel in files:
        if any(rel == u or rel.startswith(u + os.sep) for u in USER_DATA):
            continue
        if not os.path.isfile(os.path.join(src, rel)):
            continue        # 4.4.0: README, the installers etc. sit outside app/ in a shared copy
        target = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(os.path.join(src, rel), target)
        copied += 1
    return copied


def found_user_data(src, dest):
    """What the unzipped folder has that the installed one doesn't yet."""
    def has_data(p):
        if not os.path.exists(p):
            return False
        if os.path.isdir(p):
            return any(os.scandir(p))
        if os.path.basename(p) == "config.json":
            return not _is_placeholder_config(p)
        return True
    # Only what the source has AND the installed copy doesn't: never merged
    # into (or over) data that's already there.
    return [n for n in BRING_ALONG if has_data(os.path.join(src, n)) and not has_data(os.path.join(dest, n))]


def _is_placeholder_config(path):
    try:
        import json
        with open(path, encoding="utf-8") as f:
            k = (json.load(f).get("openrouter_api_key") or "").strip()
        return not k or k == "PUT-YOUR-KEY-HERE"
    except (OSError, ValueError):
        return True


def bring_user_data(src, dest, names):
    for name in names:
        s, d = os.path.join(src, name), os.path.join(dest, name)
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)


# ---- Windows: icons and the Apps entry -----------------------------------

def _powershell_shortcut(lnk, target, args, workdir, icon):
    """Creates a .lnk through Windows' own WScript.Shell; the paths travel
    as environment variables, so no quoting can go wrong."""
    env = dict(os.environ, J_LNK=lnk, J_TARGET=target, J_ARGS=args, J_DIR=workdir, J_ICON=icon)
    script = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:J_LNK);"
              "$s.TargetPath=$env:J_TARGET;$s.Arguments=$env:J_ARGS;$s.WorkingDirectory=$env:J_DIR;"
              "$s.IconLocation=$env:J_ICON;$s.Description='Jarvis';$s.Save()")
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                       env=env, capture_output=True, text=True, creationflags=0x08000000)
    return r.returncode == 0


def windows_places():
    """Desktop (even when OneDrive moved it) and the Start menu."""
    desktop = None
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
                           capture_output=True, text=True, creationflags=0x08000000)
        desktop = r.stdout.strip() or None
    except OSError:
        pass
    desktop = desktop or os.path.join(os.path.expanduser("~"), "Desktop")
    start = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs")
    return desktop, start


def windows_icons(dest, desktop_icon=True, start_menu=True):
    desktop, start = windows_places()
    args = f'"{os.path.join(dest, "jarvis_launcher.pyw")}"'
    icon = os.path.join(dest, "assets", "jarvis.ico")
    made = []
    for folder in [f for f, want in ((desktop, desktop_icon), (start, start_menu), (dest, True)) if want]:
        lnk = os.path.join(folder, "Jarvis.lnk")
        if os.path.isdir(folder) and _powershell_shortcut(lnk, pythonw(), args, dest, icon):
            made.append(lnk)
    return made


def windows_register(dest):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
        for name, value in (("DisplayName", "Jarvis"), ("DisplayVersion", VERSION), ("Publisher", "Jarvis (shared by a friend)"),
                            ("InstallLocation", dest), ("DisplayIcon", os.path.join(dest, "assets", "jarvis.ico")),
                            ("UninstallString", f'"{pythonw()}" "{os.path.join(dest, "installer.py")}" --uninstall')):
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)


def _osa(script):
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def _q(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _ask(title, text, default_no=False, yes="Yes", no="No"):
    """A yes/no question as a real dialog: Windows MessageBox, macOS dialog."""
    if IS_WINDOWS:
        import ctypes
        flags = 0x04 | 0x20 | (0x100 if default_no else 0)          # YESNO | QUESTION | (DEFBUTTON2)
        return ctypes.windll.user32.MessageBoxW(None, text, title, flags) == 6
    if IS_MAC:
        code, out = _osa(f"display dialog {_q(text)} with title {_q(title)} buttons {{{_q(no)}, {_q(yes)}}} "
                         f"default button {_q(no if default_no else yes)} with icon note")
        return code == 0 and out.endswith(yes)
    return input(f"{text} [y/n]: ").strip().lower().startswith("y")


def _tell(title, text):
    if IS_WINDOWS:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)
    elif IS_MAC:
        _osa(f"display dialog {_q(text)} with title {_q(title)} buttons {{\"OK\"}} default button \"OK\" with icon note")
    else:
        print(text)


def _launcher():
    import importlib.machinery, importlib.util
    loader = importlib.machinery.SourceFileLoader("jarvis_launcher", os.path.join(HERE, "jarvis_launcher.pyw"))
    spec = importlib.util.spec_from_loader("jarvis_launcher", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def ask_browser():
    """Which browser Jarvis should open in -- the person chooses (4.4.0)."""
    found = _launcher().available_browsers()
    options = [name for _key, name in found] + ["My normal browser"]
    keys = [key for key, _name in found] + ["default"]
    if IS_MAC:
        lst = "{" + ", ".join(_q(o) for o in options) + "}"
        code, out = _osa(f"choose from list {lst} with title \"Jarvis\" with prompt "
                         f"\"Which browser should Jarvis open in?\" default items {{{_q(options[0])}}}")
        return keys[options.index(out)] if code == 0 and out in options else keys[0]
    if IS_WINDOWS and len(options) > 1:
        for key, name in zip(keys[:-1], options[:-1]):   # one plain question per browser found
            if _ask("Jarvis", f"Open Jarvis in {name}?\n\n(No = ask about the next one, then your normal browser.)"):
                return key
        return "default"
    if len(options) > 1 and sys.stdin.isatty():
        for i, o in enumerate(options, 1):
            print(f"  {i}) {o}")
        pick = input("Which browser should Jarvis open in? [1]: ").strip() or "1"
        return keys[int(pick) - 1] if pick.isdigit() and 1 <= int(pick) <= len(keys) else keys[0]
    return keys[0]


def save_browser(dest, key):
    import setup_flow
    setup_flow.save(os.path.join(dest, "config.json"), {"app_browser": key})


# ---- Mac: Jarvis.app ------------------------------------------------------

INFO_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Jarvis</string>
  <key>CFBundleDisplayName</key><string>Jarvis</string>
  <key>CFBundleIdentifier</key><string>local.jarvis.launcher</string>
  <key>CFBundleVersion</key><string>{version}</string>
  <key>CFBundleShortVersionString</key><string>{version}</string>
  <key>CFBundleExecutable</key><string>Jarvis</string>
  <key>CFBundleIconFile</key><string>jarvis</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>10.13</string>
</dict></plist>
"""


def mac_app(dest, apps_dir=None):
    """~/Applications/Jarvis.app: a small launcher that runs Jarvis from the
    install folder (the notes stay there, not inside the app)."""
    apps_dir = apps_dir or os.path.expanduser("~/Applications")
    app = os.path.join(apps_dir, "Jarvis.app")
    if os.path.isdir(app):
        shutil.rmtree(app)
    os.makedirs(os.path.join(app, "Contents", "MacOS"))
    os.makedirs(os.path.join(app, "Contents", "Resources"))
    with open(os.path.join(app, "Contents", "Info.plist"), "w", encoding="utf-8") as f:
        f.write(INFO_PLIST.format(version=VERSION))
    exe = os.path.join(app, "Contents", "MacOS", "Jarvis")
    with open(exe, "w", encoding="utf-8", newline="\n") as f:
        f.write(f'#!/bin/bash\nexec "{sys.executable}" "{os.path.join(dest, "jarvis_launcher.pyw")}"\n')
    os.chmod(exe, 0o755)
    shutil.copy2(os.path.join(dest, "assets", "jarvis.icns"), os.path.join(app, "Contents", "Resources", "jarvis.icns"))
    return app


# ---- the steps --------------------------------------------------------------

def launch(dest):
    kw = {"creationflags": 0x00000008} if IS_WINDOWS else {"start_new_session": True}
    subprocess.Popen([pythonw(), os.path.join(dest, "jarvis_launcher.pyw")], cwd=dest,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)


def stop_running():
    """An update shouldn't pull the rug from under a running Jarvis."""
    import urllib.request
    try:
        urllib.request.urlopen(urllib.request.Request("http://localhost:4700/system/quit", data=b"{}", method="POST",
                                                      headers={"Content-Type": "application/json"}), timeout=2)
        time.sleep(1.5)
        return True
    except Exception:
        return False


def install(src=HERE, dest=None, ask=True, start=True, options=None):
    """options: {"desktop": bool, "startmenu": bool, "applications": bool,
    "browser": key}. Anything not given is ASKED (4.4.0), or defaulted when
    ask=False."""
    dest = dest or install_dir()
    o = dict(options or {})
    if IS_WINDOWS:
        if "startmenu" not in o:
            o["startmenu"] = not ask or _ask("Install Jarvis", "Add Jarvis to the Start menu?")
        if "desktop" not in o:
            o["desktop"] = not ask or _ask("Install Jarvis", "Add a Jarvis shortcut to your Desktop?")
    if IS_MAC and "applications" not in o:
        o["applications"] = not ask or _ask("Install Jarvis", "Add Jarvis to your Applications folder, so you can open it "
                                            "from Launchpad and Spotlight?", yes="Add", no="Don't add")
    if "browser" not in o:
        o["browser"] = ask_browser() if ask else "auto"
    updating = os.path.exists(os.path.join(dest, "server.py"))
    print(f"{'Updating' if updating else 'Installing'} Jarvis {VERSION} in {dest}")
    stop_running()
    os.makedirs(dest, exist_ok=True)
    n = copy_program(src, dest)
    print(f"  {n} program files copied. Your notes, key and history are never overwritten.")
    extra = [] if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(dest)) else found_user_data(src, dest)
    if extra and (not ask or _ask("Jarvis", "This folder has your Jarvis notes/settings (" + ", ".join(extra)
                                  + "). Bring them into the installed Jarvis? (They're copied, not moved.)")):
        bring_user_data(src, dest, extra)
        print("  Brought across: " + ", ".join(extra))
    save_browser(dest, o["browser"])
    finish(dest, desktop_icon=o.get("desktop", True), start_menu=o.get("startmenu", True),
           applications=o.get("applications", True))
    if start:
        launch(dest)
    return dest


def finish(dest, register=True, desktop_icon=True, start_menu=True, applications=True):
    if IS_WINDOWS:
        made = windows_icons(dest, desktop_icon, start_menu)
        print(f"  Shortcuts made: {len(made)}")
        if not register:        # Setup.exe has its own entry in Settings > Apps
            return
        try:
            windows_register(dest)
            print("  Added to Settings > Apps (with Uninstall)")
        except OSError as e:
            print(f"  (Couldn't add to Settings > Apps: {e})")
    elif IS_MAC and applications:
        print(f"  App: {mac_app(dest)}")


def uninstall(dest=None, ask=True):
    dest = dest or (HERE if os.path.exists(os.path.join(HERE, "server.py")) else install_dir())
    if ask and not _ask("Uninstall Jarvis", "Remove Jarvis from this computer?\n\nYour notes are kept unless you say otherwise next."):
        return False
    stop_running()
    keep_notes = not ask or not _ask("Uninstall Jarvis", "Delete your notes and settings too?\n\n"
                                     "Choose No to keep them (in " + dest + ").", default_no=True)
    for rel in make_share.files_to_share():
        if rel != "installer.py":
            p = os.path.join(dest, rel)
            if os.path.isfile(p):
                os.remove(p)
    for sub in ("viewer", "examples", "browser_launchers", "assets", "installer", "__pycache__"):
        p = os.path.join(dest, sub)
        if os.path.isdir(p) and (not keep_notes or sub != "browser_launchers"):
            shutil.rmtree(p, ignore_errors=True)
    if not keep_notes:
        for name in USER_DATA:
            p = os.path.join(dest, name)
            shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else (os.path.exists(p) and os.remove(p))
    if IS_WINDOWS:
        desktop, start = windows_places()
        for lnk in (os.path.join(desktop, "Jarvis.lnk"), os.path.join(start, "Jarvis.lnk"), os.path.join(dest, "Jarvis.lnk")):
            if os.path.exists(lnk):
                os.remove(lnk)
        try:
            import winreg
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
        except OSError:
            pass
    elif IS_MAC:
        shutil.rmtree(os.path.expanduser("~/Applications/Jarvis.app"), ignore_errors=True)
    try:
        os.remove(os.path.join(dest, "installer.py"))
    except OSError:
        pass
    _tell("Jarvis", "Jarvis has been removed." + (f"\n\nYour notes are still in:\n{dest}" if keep_notes else ""))
    return True


def _flag(name):
    for a in sys.argv:
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return None


if __name__ == "__main__":
    if "--uninstall" in sys.argv:
        uninstall()
    elif "--in-place" in sys.argv:
        # Setup.exe: the files and shortcuts are its job; this only records
        # the browser that was chosen in the wizard.
        save_browser(HERE, _flag("--browser") or "auto")
    else:
        opts = {}
        if _flag("--browser"):
            opts["browser"] = _flag("--browser")
        if "--no-desktop" in sys.argv:
            opts["desktop"] = False
        if "--no-startmenu" in sys.argv:
            opts["startmenu"] = False
        install(ask="--yes" not in sys.argv, options=opts)
