"""
windows_focus.py — frontmost (focused) window detection, Windows-only.

This is the piece that lets Jarvis know "what app are you actually looking
at right now" — which app is in front, and whether it's a browser at all
(see focus_session._read_surface for how that decides between an app-level
and a tab-level identity).

2.1.0 REWRITE — standard library only (ctypes), no pywin32:
The original version needed `pip install pywin32`. That was never installed
on the machine this actually runs on, so frontmost-app detection silently
never worked there: the focus system could only ever see browser tabs, so
switching to a non-browser app was invisible, and Prompt 11's "from a
non-browser app, lock the app" was impossible. ctypes ships with every
Windows Python, so this now works out of the box with nothing to install.

It still degrades gracefully everywhere else: importing never raises, and
get_frontmost_window() returns None off Windows, so nothing else has to
special-case the platform.

WHAT CAN AND CAN'T BE TESTED IN THE DEV SANDBOX:
The sandbox is headless Linux, so the non-Windows fallback path is
exercised for real here. The actual Win32 calls (GetForegroundWindow,
GetWindowThreadProcessId, QueryFullProcessImageNameW) only run on the
user's own Windows machine — they're standard, documented user32/kernel32
calls with explicit ctypes signatures, but they are verified live there,
not here.
"""

import os
import sys

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _user32.GetForegroundWindow.argtypes = []
    _user32.GetForegroundWindow.restype = wintypes.HWND

    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetWindowTextLengthW.restype = ctypes.c_int

    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowTextW.restype = ctypes.c_int

    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD

    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE

    _kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL

    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL

    # Prompt 12: an app's display name comes from the "File description"
    # built into its .exe (what Task Manager shows: "Discord", "Visual
    # Studio Code"), read with the standard Windows version-info calls.
    _version = ctypes.WinDLL("version", use_last_error=True)
    _version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    _version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    _version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    _version.GetFileVersionInfoW.restype = wintypes.BOOL
    _version.VerQueryValueW.argtypes = [
        ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT),
    ]
    _version.VerQueryValueW.restype = wintypes.BOOL

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    # 2.3.0 (usage tracker): time since the last keyboard/mouse input, so
    # time you're away from the computer isn't counted as time in whatever
    # window happens to be in front.
    class _LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    _user32.GetLastInputInfo.argtypes = [ctypes.POINTER(_LASTINPUTINFO)]
    _user32.GetLastInputInfo.restype = wintypes.BOOL
    _kernel32.GetTickCount.argtypes = []
    _kernel32.GetTickCount.restype = wintypes.DWORD


def frontmost_available():
    """Returns (True, None) if we can read the focused window on this
    machine, or (False, reason) if not."""
    if not IS_WINDOWS:
        return False, f"frontmost-window detection is Windows-only (this is {sys.platform})"
    return True, None


def _process_path_for_pid(pid):
    """Full executable path for a pid, or None."""
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return buf.value or None
    finally:
        _kernel32.CloseHandle(handle)


def _file_description(exe_path):
    """The .exe's own "File description" ("Discord"), or None. Looked up
    fresh each time -- nothing is cached, so no app name is kept around
    between reads (Prompt 12's privacy law)."""
    try:
        size = _version.GetFileVersionInfoSizeW(exe_path, None)
        if not size:
            return None
        data = ctypes.create_string_buffer(size)
        if not _version.GetFileVersionInfoW(exe_path, 0, size, data):
            return None

        ptr = ctypes.c_void_p()
        length = wintypes.UINT()
        codepages = []
        if _version.VerQueryValueW(data, "\\VarFileInfo\\Translation", ctypes.byref(ptr), ctypes.byref(length)) \
                and length.value >= 4:
            words = ctypes.cast(ptr, ctypes.POINTER(wintypes.WORD))
            for i in range(0, length.value // 2, 2):
                codepages.append(f"{words[i]:04x}{words[i + 1]:04x}")
        codepages += ["040904b0", "040904e4", "000004b0"]  # common fallbacks

        for cp in codepages:
            if _version.VerQueryValueW(data, f"\\StringFileInfo\\{cp}\\FileDescription",
                                       ctypes.byref(ptr), ctypes.byref(length)) and length.value > 1:
                text = ctypes.wstring_at(ptr.value, length.value).rstrip("\x00").strip()
                if text:
                    return text
    except Exception:
        pass
    return None


def get_frontmost_window():
    """
    Returns {"title": str, "process": str, "pid": int, "display_name": str|None}
    for whatever window currently has OS focus, or None if unavailable.
    display_name (Prompt 12) is the app's own File description. A fresh OS query every
    call — no caching, per the Prompt 09 spec. Never raises: a failure here
    should degrade the focus feature, not crash the server.
    """
    if not IS_WINDOWS:
        return None
    try:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return None
        length = _user32.GetWindowTextLengthW(hwnd)
        title_buf = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, title_buf, length + 1)
        pid = wintypes.DWORD(0)
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        path = _process_path_for_pid(pid.value)
        process = os.path.basename(path) if path else "unknown"
        # Store-style apps (Calculator, Settings...) all run inside
        # ApplicationFrameHost.exe, whose own description is useless; the
        # window title is their real name there.
        if process.lower() == "applicationframehost.exe":
            display_name = title_buf.value or None
        else:
            display_name = _file_description(path) if path else None
        return {
            "title": title_buf.value,
            "process": process,
            "pid": pid.value,
            "display_name": display_name,
        }
    except Exception:
        return None


def idle_seconds():
    """Seconds since the last keyboard or mouse input on this computer, or
    None where that can't be read (not Windows, or the call failed). Both
    counters are 32-bit milliseconds that wrap every ~49.7 days; the
    subtraction is done modulo 2**32 so the wrap doesn't matter."""
    if not IS_WINDOWS:
        return None
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not _user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        return ((_kernel32.GetTickCount() - info.dwTime) % 2**32) / 1000.0
    except Exception:
        return None


if __name__ == "__main__":
    ok, reason = frontmost_available()
    if not ok:
        print(f"Frontmost-window detection unavailable here: {reason}")
    else:
        info = get_frontmost_window()
        print(info if info else "Could not read the current foreground window.")
        print(f"Seconds since your last keyboard/mouse input: {idle_seconds()}")
