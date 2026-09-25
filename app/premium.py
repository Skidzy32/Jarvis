"""
premium.py — 4.9.0 (addendum §19, the user's choice "both"): an OPTIONAL
stronger model in the pool. Off unless you switch it on in Settings; nothing
changes for anyone who doesn't.

  mode "claude_code"    Claude Code on this computer, signed in with YOUR
                        Claude plan (Pro includes Claude Code; it does NOT
                        include the API). Jarvis runs `claude -p` with the
                        task brief on stdin, in an empty folder, and uses its
                        reply. Uses your plan's limits. Check it fits
                        Anthropic's terms for how you use it.
  mode "anthropic_api"  an Anthropic API key (pay per use, billed separately)
  mode "openrouter"     a paid model on OpenRouter (your OpenRouter credit)

Used for:  "reasoning"  the harder questions (compare / decide / plan / why)
           "rescue"     only when every free model has failed
with a daily cap (default 20 requests). The same task brief goes to it as to
the free models: personality, session state, only the relevant notes, private
details hidden. It never gets tools: like every model, it can only talk.

The Anthropic key lives in config.json next to your OpenRouter key; Jarvis
never shows it back, and it's never in a shared copy.
Standard library only.
"""

import datetime
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request

import records

MODES = ("off", "claude_code", "anthropic_api", "openrouter")
USES = ("reasoning", "rescue")
DEFAULTS = {"mode": "off", "model": "", "daily_cap": 20, "use_for": ["reasoning", "rescue"]}
DEFAULT_MODELS = {"anthropic_api": "claude-sonnet-5", "claude_code": "", "openrouter": ""}
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
TIMEOUT_S = 120
USAGE_FILE = "premium_usage.json"


def settings(config):
    p = dict(DEFAULTS)
    p.update({k: v for k, v in (config.get("premium") or {}).items() if k in DEFAULTS})
    if p["mode"] not in MODES:
        p["mode"] = "off"
    p["use_for"] = [u for u in p.get("use_for") or [] if u in USES]
    try:
        p["daily_cap"] = max(0, min(500, int(p["daily_cap"])))
    except (TypeError, ValueError):
        p["daily_cap"] = DEFAULTS["daily_cap"]
    return p


def validate_changes(changes):
    """For Settings -> config.json. Raises ValueError on anything odd."""
    out = {}
    if "mode" in changes:
        if changes["mode"] not in MODES:
            raise ValueError("unknown option for the stronger AI")
        out["mode"] = changes["mode"]
    if "model" in changes:
        m = str(changes["model"] or "").strip()
        if len(m) > 120 or any(c.isspace() for c in m):
            raise ValueError("that model name doesn't look right")
        out["model"] = m
    if "daily_cap" in changes:
        try:
            out["daily_cap"] = max(0, min(500, int(changes["daily_cap"])))
        except (TypeError, ValueError):
            raise ValueError("the daily limit is a number")
    if "use_for" in changes:
        out["use_for"] = [u for u in changes["use_for"] or [] if u in USES]
    return out


def claude_code_path():
    return shutil.which("claude") or shutil.which("claude.cmd") or shutil.which("claude.exe")


def status(config):
    """What Settings shows. Never includes the key itself."""
    p = settings(config)
    k = (config.get("anthropic_api_key") or "").strip()
    return dict(p, has_anthropic_key=bool(k), claude_code_found=bool(claude_code_path()),
                used_today=used_today(), model_shown=p["model"] or DEFAULT_MODELS.get(p["mode"], ""))


# ---- daily cap ------------------------------------------------------------------------

def _usage_path(notes_dir=None):
    return os.path.join(records.store_dir(notes_dir), USAGE_FILE)


def used_today(notes_dir=None, today=None):
    today = (today or datetime.date.today()).isoformat()
    try:
        with open(_usage_path(notes_dir), encoding="utf-8") as f:
            return int(json.load(f).get(today, 0))
    except (OSError, ValueError, TypeError):
        return 0


def count_use(notes_dir=None, today=None):
    today = (today or datetime.date.today()).isoformat()
    try:
        with open(_usage_path(notes_dir), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d = {k: v for k, v in d.items() if k >= (datetime.date.fromisoformat(today) - datetime.timedelta(days=31)).isoformat()}
    d[today] = int(d.get(today, 0)) + 1
    try:
        records._atomic_write_json(_usage_path(notes_dir), d)
    except OSError:
        pass


# ---- in the pool ------------------------------------------------------------------------

def model_id(p):
    if p["mode"] == "claude_code":
        return "claude-code" + (f":{p['model']}" if p["model"] else "")
    if p["mode"] == "anthropic_api":
        return "anthropic:" + (p["model"] or DEFAULT_MODELS["anthropic_api"])
    return p["model"]


def available(config, notes_dir=None, today=None):
    """(ready, reason)."""
    p = settings(config)
    if p["mode"] == "off":
        return False, "off"
    if p["daily_cap"] and used_today(notes_dir, today) >= p["daily_cap"]:
        return False, "daily limit reached"
    if p["mode"] == "claude_code" and not claude_code_path():
        return False, "Claude Code isn't installed on this computer"
    if p["mode"] == "anthropic_api" and not (config.get("anthropic_api_key") or "").strip():
        return False, "no Anthropic API key"
    if p["mode"] == "openrouter" and not p["model"]:
        return False, "no OpenRouter model chosen"
    return True, "ready"


def profile(config, cap, notes_dir=None, today=None):
    """[profile] to add to the pool for this capability, or []."""
    p = settings(config)
    ok, _ = available(config, notes_dir, today)
    if not ok or "reasoning" not in p["use_for"] or cap not in ("REASONING",):
        return []
    return [{"id": model_id(p), "name": "Stronger AI (yours)", "caps": ["GENERAL", "REASONING"],
             "premium": True, "vision": False, "tools": False}]


def rescue_id(config, notes_dir=None, today=None):
    p = settings(config)
    ok, _ = available(config, notes_dir, today)
    return model_id(p) if ok and "rescue" in p["use_for"] else None


def is_premium(model_id_, config):
    p = settings(config)
    return p["mode"] != "off" and model_id_ == model_id(p)


# ---- calling it ---------------------------------------------------------------------------

def _flatten(messages):
    """Task packet -> one prompt text (for Claude Code)."""
    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            c = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
        role = {"system": "BRIEF", "user": "User", "assistant": "Jarvis"}.get(m.get("role"), m.get("role"))
        out.append(f"{role}:\n{c}")
    out.append("Reply as Jarvis to the user's last message. Reply with the answer only.")
    return "\n\n".join(out)


def call(config, messages, run=None, urlopen=None):
    """(answer, model_used). Raises RuntimeError with a plain reason."""
    p = settings(config)
    mid = model_id(p)
    if p["mode"] == "claude_code":
        exe = claude_code_path()
        if not exe and run is None:
            raise RuntimeError("Claude Code isn't installed on this computer")
        cmd = [exe or "claude", "-p", "--output-format", "json"] + (["--model", p["model"]] if p["model"] else [])
        work = tempfile.mkdtemp(prefix="jarvis-cc-")          # an empty folder: nothing of yours to look at
        try:
            r = (run or subprocess.run)(cmd, input=_flatten(messages), capture_output=True, text=True,
                                        timeout=TIMEOUT_S, cwd=work,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        finally:
            shutil.rmtree(work, ignore_errors=True)
        if r.returncode != 0:
            raise RuntimeError(f"Claude Code didn't answer ({(r.stderr or r.stdout or '').strip()[:160]})")
        text = r.stdout.strip()
        try:
            d = json.loads(text)
            if d.get("is_error"):
                raise RuntimeError(f"Claude Code: {str(d.get('result'))[:160]}")
            text = d.get("result") or ""
        except ValueError:
            pass
        return text.strip(), mid
    if p["mode"] == "anthropic_api":
        system = "\n\n".join(m["content"] for m in messages if m.get("role") == "system" and isinstance(m.get("content"), str))
        msgs = [{"role": m["role"], "content": m["content"]} for m in messages if m.get("role") in ("user", "assistant")]
        body = json.dumps({"model": p["model"] or DEFAULT_MODELS["anthropic_api"], "max_tokens": 1024,
                           "system": system, "messages": msgs}).encode()
        req = urllib.request.Request(ANTHROPIC_URL, data=body, method="POST", headers={
            "content-type": "application/json", "anthropic-version": "2023-06-01",
            "x-api-key": (config.get("anthropic_api_key") or "").strip()})
        try:
            with (urlopen or urllib.request.urlopen)(req, timeout=TIMEOUT_S) as resp:
                d = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"the Anthropic API said HTTP {e.code}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"couldn't reach the Anthropic API ({e.reason})")
        text = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
        return text.strip(), mid
    raise RuntimeError("not a direct-call option")          # openrouter mode goes through the normal OpenRouter call
