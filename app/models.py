"""
models.py — 4.8.0 (addendum R2 + R3's learning): the model pool.

Before 4.8.0 every message went to OpenRouter's own "openrouter/free" router,
which could pick a different model each time. Now Jarvis keeps its own pool:

  PROFILES   the free models on OpenRouter (its public model list, refreshed
             at most once a day, kept in notes/.jarvis/models.json): context
             size, whether it reads images, tool / structured-output support,
             reasoning support -> capability classes
                FAST       short chat, greetings, quick sorting
                GENERAL    ordinary conversation and questions
                REASONING  multi-step, comparing, deciding, planning
                VISION     photos, screenshots, paper
                TOOL       JSON plans and structured output
  CHOICE     for each request: the capability it needs -> the eligible
             models -> the one that has done best at that capability here,
             preferring the one already answering this conversation
             (session affinity) while it keeps passing. Not A->B->C->A.
  LEARNING   every call is recorded per model x capability (success,
             failure and why, latency, retries) in model_stats.json. Three
             failures in a row at a capability benches the model for THAT
             capability for a while (quarantine); it stays usable for the
             rest. A model that's gone (404) is benched for everything for a
             day; a rate-limited one (429) for ten minutes.
  FALLBACK   "openrouter/free" is always the last resort, so Jarvis still
             works if the list can't be fetched.

A stronger model (the optional Claude slot, 4.9.0) is just another profile.
Standard library only.
"""

import datetime
import json
import os
import random
import re
import threading
import time
import urllib.request

import records

MODELS_URL = "https://openrouter.ai/api/v1/models"
FALLBACK = "openrouter/free"
CAPS = ("FAST", "GENERAL", "REASONING", "VISION", "TOOL")
REFRESH_HOURS = 24
QUARANTINE_AFTER = 3                 # failures in a row at one capability
QUARANTINE_MIN = 6 * 60              # ... benched for 6 hours
GONE_MIN = 24 * 60                   # a 404: the model's gone, bench it for a day
RATE_LIMIT_MIN = 10                  # a 429: give it ten minutes
BLOCK_RE = re.compile(r"safety|guard|shield|moderat|classif|embed|rerank|tts|whisper|audio", re.I)
SMALL_RE = re.compile(r"\b(mini|small|nano|tiny|lite|flash|haiku|\d(\.\d)?b|1[0-4]b)\b", re.I)
BIG_RE = re.compile(r"\b(r1|reason|think|qwq|large|pro|70b|72b|120b|235b|405b|opus|sonnet|gpt-5|deepseek-v3)\b", re.I)
_LOCK = threading.RLock()


def _dir(notes_dir=None):
    return records.store_dir(notes_dir)


def _read(name, notes_dir=None, default=None):
    try:
        with open(os.path.join(_dir(notes_dir), name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(name, data, notes_dir=None):
    try:
        records._atomic_write_json(os.path.join(_dir(notes_dir), name), data)
    except OSError:
        pass


# ---- profiles -------------------------------------------------------------------

def _is_free(m):
    p = m.get("pricing") or {}
    try:
        return float(p.get("prompt", 1)) == 0 and float(p.get("completion", 1)) == 0
    except (TypeError, ValueError):
        return str(m.get("id", "")).endswith(":free")


def profile_from(m):
    """One OpenRouter model entry -> a Jarvis profile."""
    mid = m.get("id", "")
    name = f"{mid} {m.get('name', '')}"
    arch = m.get("architecture") or {}
    ins = [str(x).lower() for x in (arch.get("input_modalities") or [])] or \
        (["text", "image"] if "image" in str(arch.get("modality", "")).lower().split("->")[0] else ["text"])
    params = [str(x).lower() for x in (m.get("supported_parameters") or [])]
    vision = "image" in ins
    tools = "tools" in params or "tool_choice" in params
    structured = "response_format" in params or "structured_outputs" in params
    reasoning = "reasoning" in params or "include_reasoning" in params or bool(BIG_RE.search(name))
    context = int(m.get("context_length") or (m.get("top_provider") or {}).get("context_length") or 0)
    caps = ["GENERAL"]
    if SMALL_RE.search(name) or not reasoning:
        caps.append("FAST")
    if reasoning and context >= 16000:
        caps.append("REASONING")
    if vision:
        caps.append("VISION")
    if tools or structured:
        caps.append("TOOL")
    return {"id": mid, "name": m.get("name") or mid, "context": context, "vision": vision, "tools": tools,
            "structured": structured, "reasoning": reasoning, "caps": caps, "provider": mid.split("/")[0]}


def parse_list(payload, blocked=()):
    """OpenRouter's /models answer -> [profile] for usable free text models."""
    out = []
    for m in (payload or {}).get("data", []):
        mid = str(m.get("id", ""))
        if not mid or not _is_free(m) or mid == FALLBACK or BLOCK_RE.search(mid) or any(b in mid for b in blocked):
            continue
        outs = [str(x).lower() for x in ((m.get("architecture") or {}).get("output_modalities") or ["text"])]
        if "text" not in outs:
            continue
        out.append(profile_from(m))
    return out


def load_profiles(notes_dir=None):
    return (_read("models.json", notes_dir, {}) or {}).get("models", [])


def refresh(notes_dir=None, fetch=None, force=False, blocked=()):
    """Fetches the free-model list (at most daily unless forced). Returns
    (count, message). Never raises: without it Jarvis uses openrouter/free."""
    with _LOCK:
        cached = _read("models.json", notes_dir, {}) or {}
        age_h = (time.time() - cached.get("fetched_ts", 0)) / 3600
        if not force and cached.get("models") and age_h < REFRESH_HOURS:
            return len(cached["models"]), "up to date"
        try:
            if fetch:
                payload = fetch()
            else:
                with urllib.request.urlopen(urllib.request.Request(MODELS_URL, headers={"User-Agent": "Jarvis"}),
                                            timeout=20) as r:
                    payload = json.loads(r.read())
            profs = parse_list(payload, blocked)
        except Exception as e:
            return len(cached.get("models", [])), f"couldn't fetch the model list ({e.__class__.__name__}); kept the last one"
        if not profs:
            return len(cached.get("models", [])), "the list had no usable free models; kept the last one"
        _write("models.json", {"fetched": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                               "fetched_ts": time.time(), "models": profs}, notes_dir)
        return len(profs), "refreshed"


# ---- what a request needs -----------------------------------------------------------

REASON_RE = re.compile(r"\b(why|compare|pros and cons|trade-?offs?|should i|decide|decision|plan|strategy|analy[sz]e|"
                       r"explain how|step by step|conflict|contradict|which is better|weigh up|options)\b", re.I)
GREET_RE = re.compile(r"^(hi|hello|hey|morning|good (morning|afternoon|evening)|thanks|thank you|cheers|ok|okay|"
                      r"great|nice|yes|no|yep|nope)\b", re.I)


def capability_for(text="", has_image=False, needs_json=False):
    if has_image:
        return "VISION"
    if needs_json:
        return "TOOL"
    words = len(re.findall(r"\w+", text or ""))
    if REASON_RE.search(text or "") or words > 40:
        return "REASONING"
    if words <= 6 and GREET_RE.search((text or "").strip()):
        return "FAST"
    return "GENERAL"


# ---- learning -------------------------------------------------------------------------------

def load_stats(notes_dir=None):
    return _read("model_stats.json", notes_dir, {}) or {}


def _slot(stats, model, cap):
    return stats.setdefault(model, {}).setdefault(cap, {"ok": 0, "fail": 0.0, "row": 0, "ms": None,
                                                        "last": None, "why": None, "benched_until": 0})


def record(model, cap, ok, notes_dir=None, latency_ms=None, reason=None, weight=1.0, now=None):
    """One outcome. weight < 1 for weak signals (a user correction)."""
    now = now or time.time()
    with _LOCK:
        stats = load_stats(notes_dir)
        s = _slot(stats, model, cap)
        s["last"] = datetime.datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds")
        if ok:
            s["ok"] += 1
            s["row"] = 0
            if latency_ms is not None:
                s["ms"] = latency_ms if s["ms"] is None else round(0.7 * s["ms"] + 0.3 * latency_ms)
        else:
            s["fail"] = round(s["fail"] + weight, 2)
            s["why"] = reason
            if weight >= 1:
                s["row"] += 1
            if reason == "gone":
                for c in CAPS:
                    _slot(stats, model, c)["benched_until"] = now + GONE_MIN * 60
            elif reason == "rate_limited":
                for c in CAPS:
                    x = _slot(stats, model, c)
                    x["benched_until"] = max(x["benched_until"], now + RATE_LIMIT_MIN * 60)
            elif s["row"] >= QUARANTINE_AFTER:
                s["benched_until"] = now + QUARANTINE_MIN * 60
                s["row"] = 0
        _write("model_stats.json", stats, notes_dir)


def benched(stats, model, cap, now=None):
    return ((stats.get(model) or {}).get(cap) or {}).get("benched_until", 0) > (now or time.time())


def score(stats, model, cap):
    s = (stats.get(model) or {}).get(cap) or {}
    rate = (s.get("ok", 0) + 1) / (s.get("ok", 0) + s.get("fail", 0) + 2)
    speed = 1.0 if not s.get("ms") else max(0.6, min(1.0, 8000 / max(s["ms"], 1)))
    return rate * speed


# ---- choosing -----------------------------------------------------------------------------------

def candidates(cap, prefer=None, notes_dir=None, exclude=(), extra=(), now=None, rng=random):
    """Model ids to try, best first, for one capability. prefer = the model
    already answering this conversation (kept while it's eligible). extra =
    profiles added from outside the free list (the optional premium slot)."""
    stats = load_stats(notes_dir)
    profs = list(extra) + load_profiles(notes_dir)
    eligible = [p for p in profs if cap in p["caps"] and p["id"] not in exclude and not benched(stats, p["id"], cap, now)]
    if not eligible and cap in ("FAST", "REASONING"):     # fall back to plain conversation models
        eligible = [p for p in profs if "GENERAL" in p["caps"] and p["id"] not in exclude
                    and not benched(stats, p["id"], "GENERAL", now)]
    scored = sorted(((score(stats, p["id"], cap) * (1.25 if p.get("premium") else 1.0), p["id"]) for p in eligible), reverse=True)
    order = [m for _, m in scored]
    # spread load across equally good models (within 5% of the best), not always the same one
    if len(scored) > 1:
        top = [m for s, m in scored if s >= scored[0][0] * 0.95]
        first = rng.choice(top)
        order.remove(first)
        order.insert(0, first)
    if prefer and prefer in order:
        order.remove(prefer)
        order.insert(0, prefer)
    if FALLBACK not in exclude:
        order.append(FALLBACK)
    return order


def summary(notes_dir=None, now=None):
    """For /models and Settings: the pool, what each is good for, and what's benched."""
    stats = load_stats(notes_dir)
    cached = _read("models.json", notes_dir, {}) or {}
    rows = []
    for p in cached.get("models", []):
        s = stats.get(p["id"], {})
        rows.append({"id": p["id"], "caps": p["caps"],
                     "ok": sum(v.get("ok", 0) for v in s.values()),
                     "fail": round(sum(v.get("fail", 0) for v in s.values()), 1),
                     "benched": [c for c in CAPS if benched(stats, p["id"], c, now)]})
    rows.sort(key=lambda r: (-r["ok"], r["id"]))
    return {"count": len(rows), "fetched": cached.get("fetched"), "models": rows}
