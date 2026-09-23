"""
setup_flow.py — 4.0.0: first run, for anyone you share Jarvis with.

  - state():   is there a key yet? what should Jarvis call you? which OS?
               (never returns the key itself -- only whether there is one)
  - test_key(): one tiny real call to OpenRouter, so "it works" is proven
  - save():    writes ONLY these settings into config.json on this computer:
               openrouter_api_key, address, user_name
  - samples:   put the demo notes (examples/) into notes/samples/ so the
               galaxy has something in it on day one; remove them again
               (they go to Jarvis's bin, like any deleted note)
  - fix_address(): Jarvis's lines say "sir"; this turns that into madam,
               your name, or nothing, as you chose. Applied to what Jarvis
               SAYS only -- never to your notes.

Standard library only.
"""

import datetime
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
VERSION = "4.4.1"
ADDRESS_MODES = ("sir", "madam", "name", "none")
APP_BROWSERS = ("auto", "chrome", "edge", "brave", "opera", "default")   # 4.3.0: what the Jarvis icon opens
SAMPLES_SOURCE = os.path.join(ROOT, "examples")
# Keys of Jarvis's own replies that get the form of address applied.
SPOKEN_KEYS = {"spoken", "answer", "confirmation", "error", "line", "switch_note", "spoken_waiting",
               "announcements", "question", "summary_line"}


def _read(config_path):
    try:
        with open(config_path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def has_key(config, placeholder):
    k = (config.get("openrouter_api_key") or "").strip()
    return bool(k) and k != placeholder


def state(config_path, placeholder, notes_dir):
    c = _read(config_path)
    return {"has_key": has_key(c, placeholder),
            "first_run": not has_key(c, placeholder),
            "address": c.get("address") if c.get("address") in ADDRESS_MODES else "sir",
            "user_name": c.get("user_name") or "",
            "platform": "windows" if sys.platform == "win32" else "mac" if sys.platform == "darwin" else "other",
            "samples": os.path.isdir(os.path.join(notes_dir, "samples")),
            "app_browser": c.get("app_browser") if c.get("app_browser") in APP_BROWSERS else "auto",
            "version": VERSION}


def test_key(key, timeout=20):
    """-> (ok, message). One minimal real call; 401/403 means the key is wrong."""
    key = (key or "").strip()
    if not key.startswith("sk-"):
        return False, "That doesn't look like an OpenRouter key (they start with sk-or-)."
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps({"model": "openrouter/free", "max_tokens": 5,
                         "messages": [{"role": "user", "content": "Reply with OK."}]}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200, "It works." if resp.status == 200 else f"Unexpected answer (HTTP {resp.status})."
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False, "OpenRouter says that key isn't valid. Copy it again from openrouter.ai/keys."
        if e.code == 429:
            return True, "The key is valid (the free models are busy right now, which is normal)."
        return False, f"OpenRouter answered with an error (HTTP {e.code}). Try again in a minute."
    except urllib.error.URLError as e:
        return False, f"Couldn't reach OpenRouter ({e.reason}). Is the internet connected?"


def save(config_path, changes):
    """Merges only the allowed settings into config.json (atomically)."""
    c = _read(config_path)
    if "openrouter_api_key" in changes:
        k = str(changes["openrouter_api_key"] or "").strip()
        if k:
            c["openrouter_api_key"] = k
    if "address" in changes:
        if changes["address"] not in ADDRESS_MODES:
            raise ValueError("unknown form of address")
        c["address"] = changes["address"]
    if "app_browser" in changes:
        if changes["app_browser"] not in APP_BROWSERS:
            raise ValueError("unknown browser")
        c["app_browser"] = changes["app_browser"]
    if "user_name" in changes:
        c["user_name"] = re.sub(r"\s+", " ", str(changes["user_name"] or "")).strip()[:40]
    if c.get("address") == "name" and not c.get("user_name"):
        raise ValueError("type the name you'd like to be called")
    tmp = config_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(c, f, indent=2)
    os.replace(tmp, config_path)
    return c


def add_samples(notes_dir):
    dest = os.path.join(notes_dir, "samples")
    if os.path.isdir(dest):
        return "The sample notes are already in."
    if not os.path.isdir(SAMPLES_SOURCE):
        raise ValueError("the examples folder is missing")
    shutil.copytree(SAMPLES_SOURCE, dest, ignore=shutil.ignore_patterns("README*"))
    return "Sample notes added: a small made-up business, so the galaxy has something to show."


def remove_samples(notes_dir, store_dirname=".jarvis"):
    src = os.path.join(notes_dir, "samples")
    if not os.path.isdir(src):
        return "There are no sample notes to remove."
    bin_dir = os.path.join(notes_dir, store_dirname, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    shutil.move(src, os.path.join(bin_dir, "samples-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
    return "Sample notes removed (they're in Jarvis's bin). Your own notes are untouched."


def fix_address(text, mode, name=""):
    """'sir' in Jarvis's own lines -> the chosen form. Idempotent."""
    if not isinstance(text, str) or not text or mode not in ("madam", "name", "none"):
        return text
    if mode in ("madam", "name"):
        word = "madam" if mode == "madam" else (name or "").strip()
        if not word:
            return text
        text = re.sub(r"\bSir\b(?!\s+[A-Z])", word[:1].upper() + word[1:], text)   # "Sir Alex" is a title: kept
        return re.sub(r"\bsir\b", word if mode == "name" else "madam", text)
    text = re.sub(r"\s*,\s*sir\b(?=\s*[.!?,;:)—-]|\s*$)", "", text, flags=re.I)
    text = re.sub(r"(^|(?<=[.!?]\s))sir,\s*(\w)", lambda m: m.group(1) + m.group(2).upper(), text, flags=re.I)
    text = re.sub(r"\s+sir\b(?!\s+[A-Z])", "", text)                 # lower-case "sir" only
    return text


def fix_payload(payload, mode, name=""):
    """Applies fix_address to Jarvis's own reply fields only."""
    if mode not in ("madam", "name", "none") or not isinstance(payload, dict):
        return payload
    out = dict(payload)
    for k, v in payload.items():
        if k in SPOKEN_KEYS:
            if isinstance(v, str):
                out[k] = fix_address(v, mode, name)
            elif isinstance(v, list):
                out[k] = [fix_address(x, mode, name) if isinstance(x, str) else x for x in v]
    return out
