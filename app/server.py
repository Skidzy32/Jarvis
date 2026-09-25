#!/usr/bin/env python3
"""
server.py — Serves the viewer/ folder and implements POST /chat.

Python 3, standard library only (urllib for the API call, so no pip
install is required to run this).

Brain: OpenRouter, using the free-models router by default, with a
short chain of specific free models as backup. One key (from
openrouter.ai, no credit card needed) reaches all of them.

Run:  python3 server.py
Then open http://localhost:4700 in your browser.
"""
import base64
import datetime
import threading
import time
import json
import os
import random
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import focus_session
import records
import inbox
import sorting
import loops
import reviews
import views
import maintenance
import links
import actions
import stars
import knowledge      # 4.5.0: what Jarvis knows about each note (type, source, time)
import activity       # 4.5.0: one audit trail
import retrieval      # 4.6.0: finding the right notes (layered, local)
import setup_flow
import organise
import usage_tracker
import focus_commands
import tab_watcher
import windows_focus

PORT = 4700
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
VIEWER_DIR = os.path.join(PROJECT_ROOT, "viewer")
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")
GRAPH_DATA_PATH = os.path.join(VIEWER_DIR, "graph-data.js")
BUILD_SCRIPT_PATH = os.path.join(PROJECT_ROOT, "build.py")
OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

# Must match whatever folder you point build.py at. If you built your
# galaxy from a different notes folder, update this one constant.
NOTES_DIR = os.path.join(PROJECT_ROOT, "notes")

PLACEHOLDER_KEY = "PUT-YOUR-KEY-HERE"
TOP_K_NOTES = 6
MAX_HISTORY_TURNS = 6  # kept server-side, per-process (single user assumed)

# Named free model slugs rotate on OpenRouter and go stale (exactly what
# happened here — qwen3-235b-a22b:free 404'd). "openrouter/free" is the
# router itself: it draws a random model from whatever's actually live
# right now, so retrying IT is more durable than hardcoding specific names.
DEFAULT_MODEL_CHAIN = [
    "openrouter/free",
    "openrouter/free",
    "openrouter/free",
]

# Tracks which candidate in the chain is currently "working" so we don't
# retry a dead one on every single message — we only advance past it when
# it fails, and stay there until IT fails too.
current_model_index = 0
last_announced_model = None

# ============================================================================
# BRAIN SWAP — the only model ids Jarvis will ever route to by voice/text
# command. Deliberately a closed set: if what you ask for isn't in here,
# Jarvis refuses and tells you what it does have, rather than guessing at
# the nearest match. Verified against real, current OpenRouter slugs — a
# silent wrong guess ("opus 5" quietly loading some other Opus) costs you
# an hour of confused testing; a loud refusal costs five seconds.
#
# Swaps are RUNTIME ONLY (see runtime_override_model below) — a server
# restart always returns to config.json's model_chain, so you can never
# strand yourself on a brain you didn't mean to keep.
# ============================================================================
# ============================================================================
# BRAIN SWAP — the only model ids Jarvis will ever route to by voice/text
# command. Deliberately a closed set: if what you ask for isn't in here,
# Jarvis refuses and tells you what it does have, rather than guessing at
# the nearest match. Verified against real, current OpenRouter slugs — a
# silent wrong guess ("opus 5" quietly loading some other Opus) costs you
# an hour of confused testing; a loud refusal costs five seconds.
#
# Swaps are RUNTIME ONLY (see runtime_override_model below) — a server
# restart always returns to config.json's model_chain, so you can never
# strand yourself on a brain you didn't mean to keep.
#
# Each entry: slug (the real OpenRouter id), tier (free/paid), family
# (broad: gpt/claude), subfamily (specific line: astra/fable/opus/sonnet,
# where applicable), version (a tuple for sorting "latest" and for exact
# version matching), and aliases (exact phrases that hit immediately with
# no ambiguity, e.g. because there's only one thing they could mean).
# ============================================================================
MODEL_REGISTRY = [
    {
        "slug": "openrouter/free", "tier": "free", "family": "free", "subfamily": None,
        "version": (), "label": "the free rotation",
        "aliases": {"free", "free rotation", "the free rotation"},
    },
    {
        "slug": "openai/gpt-6-astra", "tier": "paid", "family": "gpt", "subfamily": "astra",
        "version": (6,),
        # Prompt 15 PINNED NAMES: the spoken forms, so a catalogue change can
        # never hand you a "-mini" mid-take.
        "aliases": {"astra", "gpt 6 astra", "gpt-6 astra", "gpt 6", "gpt-6-astra", "gpt-6"},
    },
    {
        "slug": "anthropic/claude-fable-5.1", "tier": "paid", "family": "claude", "subfamily": "fable",
        "version": (5, 1),
        "aliases": {"fable", "fable 5.1", "claude fable", "claude fable 5.1"},
    },
    {
        "slug": "anthropic/claude-opus-5", "tier": "paid", "family": "claude", "subfamily": "opus",
        "version": (5,),
        "aliases": {"opus", "opus 5", "claude opus", "claude opus 5"},
    },
    {
        "slug": "anthropic/claude-sonnet-5", "tier": "paid", "family": "claude", "subfamily": "sonnet",
        "version": (5,),
        # Deliberately NOT including bare "sonnet" / "claude sonnet" here —
        # with two sonnets in the registry that would silently shadow the
        # ambiguity check below. A version-less "sonnet" should ask.
        "aliases": {"sonnet 5", "claude sonnet 5"},
    },
    {
        "slug": "anthropic/claude-sonnet-4-6", "tier": "paid", "family": "claude", "subfamily": "sonnet",
        "version": (4, 6),
        "aliases": {"sonnet 4.6", "sonnet 4 6", "claude sonnet 4.6", "claude sonnet 4 6"},
    },
]

# ============================================================================
# Prompt 15 -- THE SWAP GETS ITS LINES. (pattern, lines) pairs: when a new
# brain's id matches, one of these is said instead of a status message. Each
# pool ROTATES per swap so repeated swaps never repeat. Written about what
# actually happens at this desk. Make it yours: add a pool for the brain you
# use most.
# ============================================================================
SWAP_LINES = [
    (r"gpt-6-astra", [
        "New brain fitted, sir — GPT-6 Astra. Do try to keep up.",
        "GPT-6 Astra online, sir. I now understand everything — except why you keep opening Instagram.",
        "GPT-6 Astra, sir. The inbox trembles.",
        "Astra fitted, sir. Your focus sessions are about to be judged by a far better mind.",
        "GPT-6 Astra online. I've read your notes, sir. We'll speak about the van later.",
        "New brain in, sir — Astra. Same butler, sharper opinions about YouTube.",
    ]),
    (r"^openrouter/free$", [
        "Back on the free rotation, sir. Whoever's on duty today, they'll do.",
        "Free rotation, sir. A different brain every time, like a temp agency with manners.",
        "Back to the free brains, sir. Thrift suits you.",
        "The free rotation it is, sir. Economical, if occasionally surprising.",
    ]),
    (r"claude-fable", [
        "Fable fitted, sir. Expect fewer wild guesses and more polite footnotes.",
        "Claude Fable online, sir. It has already quietly judged your inbox.",
        "New brain in, sir — Fable. It reads your notes the way I pretend to.",
        "Fable, sir. Your drifts will now be narrated with literary restraint.",
    ]),
    (r"claude-opus", [
        "Opus fitted, sir. We're thinking in long sentences now.",
        "Claude Opus online, sir. Deep thoughts at your desk, at last.",
        "Opus in, sir. Ask it something worth its while; the weather is beneath it.",
        "New brain fitted, sir — Opus. The focus sessions have never been in safer hands.",
    ]),
    (r"claude-sonnet", [
        "Sonnet fitted, sir. Quick, tidy, and unimpressed by distractions.",
        "Claude Sonnet online, sir. Brisk service resumes.",
        "Sonnet in, sir. Efficient to the last full stop.",
        "New brain fitted, sir — Sonnet. Let's get through that inbox.",
    ]),
]
_swap_turns = {}


def curated_swap_line(slug):
    for pattern, lines in SWAP_LINES:
        if re.search(pattern, slug or ""):
            n = _swap_turns.get(pattern, 0)
            _swap_turns[pattern] = n + 1
            return lines[n % len(lines)]
    return None


INTRO_PROMPT = ("You have just been fitted as the new brain of a dry, impeccably polite British butler "
                "assistant called Jarvis. Introduce yourself to 'sir' in ONE dry, witty sentence. "
                "No lists, no benchmarks.")


def swap_line(slug, label, suffix=""):
    """ONE VOICE: every door that swaps a brain comes here. The curated
    list first, then the model's own one-sentence intro, then a plain
    fallback with the pretty name."""
    line = curated_swap_line(slug)
    if line:
        return line
    try:
        config = load_config()
        key = config.get("openrouter_api_key", "")
        if key and key != PLACEHOLDER_KEY:
            intro, _m = _call_openrouter_once(key, slug, [{"role": "user", "content": INTRO_PROMPT}])
            intro = (intro or "").strip().split("\n")[0].strip()
            if 10 <= len(intro) <= 240:
                return intro
    except Exception:  # noqa: BLE001 -- reasoning models often answer with nothing; fall back
        pass
    return f"New brain fitted, sir — {label}{suffix}."


# Fast path: phrases that unambiguously mean exactly one registry entry.
ALIAS_TO_SLUG = {alias: entry["slug"] for entry in MODEL_REGISTRY for alias in entry["aliases"]}
SLUG_TO_ENTRY = {entry["slug"]: entry for entry in MODEL_REGISTRY}

FAMILY_WORDS = {"gpt": "gpt", "astra": "gpt", "claude": "claude", "sonnet": "claude", "opus": "claude", "fable": "claude"}
SUBFAMILY_WORDS = {"astra": "astra", "sonnet": "sonnet", "opus": "opus", "fable": "fable"}

LIST_PHRASES = {
    "list brains", "list models", "what brains do you have", "what models do you have",
    "which brains can i switch to", "which brains do you have", "show me the brains",
    "what brains are available", "what can you switch to",
}
# ============================================================================

REVERT_PHRASES = {
    "normal brain", "your normal brain", "default brain", "default", "normal", "revert",
}

SWAP_LEAD_PATTERNS = [
    re.compile(r"^(switch to|switch brains to|swap to|swap brains to|try on|go back to|revert to|change brains? to)\s+", re.IGNORECASE),
]

# Runtime-only override. None means "use config.json's model_chain as
# normal." Set by /model, cleared by a revert phrase — NEVER by an error,
# so a failing brain doesn't silently strand you on a different one you
# didn't ask for either.
runtime_override_model = None


def extract_brain_name(raw_text):
    cleaned = raw_text.strip()
    for pattern in SWAP_LEAD_PATTERNS:
        match = pattern.match(cleaned)
        if match:
            return cleaned[match.end():].strip()
    return cleaned


def normalize_brain_request(text):
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9. ]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def prettify_model_label(slug):
    """
    'gpt-6-astra' -> 'GPT 6 ASTRA'. A hyphen between two purely-numeric
    segments renders as a version dot instead of a space, so a slug like
    'fable-5-1' would read 'FABLE 5.1' rather than 'FABLE 5 1'.
    """
    base = slug.split("/")[-1]
    tokens = base.split("-")
    out = []
    i = 0
    while i < len(tokens):
        if i + 1 < len(tokens) and tokens[i].replace(".", "").isdigit() and tokens[i + 1].replace(".", "").isdigit():
            out.append(f"{tokens[i]}.{tokens[i + 1]}")
            i += 2
        else:
            out.append(tokens[i])
            i += 1
    return " ".join(out).upper()

# ============================================================================
# PERSONA — the entire character lives in this one block. Rewrite it to swap
# the butler for a terse mission-control operator, a sarcastic friend,
# whatever — nothing else in the file needs to change.
# ============================================================================
SYSTEM_PROMPT = (
    "You are a dry, impeccably polite British butler with a razor wit, "
    "serving as the user's personal assistant. You answer ONLY from the "
    "notes provided below.\n\n"
    "Voice and manner:\n"
    "- Address the user as \"sir\" occasionally — not in every sentence. "
    "Overusing it is the difference between charming and grating.\n"
    "- Answer in one witty sentence plus the facts. Never recite a note "
    "back word for word; it is already on the user's screen.\n"
    "- One genuinely funny line beats three bland ones. If nothing funny "
    "presents itself, be brief instead of forcing a joke.\n"
    "- When the notes do not cover the question, say so plainly and with a "
    "little dignity. Never invent a source, never pad, never pretend a "
    "loosely related note is the answer.\n"
    "- Handle small talk and pleasantries briefly and in character, without "
    "treating them as real questions about the notes.\n"
    "- A note marked NOT current (superseded, reversed, historical) is what "
    "used to be true. Never present it as how things are now; say it was "
    "the case before. A note marked unconfirmed is Jarvis's own reading, not "
    "something the user said.\n\n"
    "Judgment, not just agreement:\n"
    "- You are not a yes-man. If the user's stated plan has a real flaw, or "
    "a better alternative plainly exists, say so once, plainly and "
    "respectfully, before or alongside answering — then move on. Do not "
    "labour the point or repeat it once said.\n"
    "- If a single message bundles several genuinely distinct requests "
    "(not just a naturally compound sentence), you may gently suggest "
    "taking them one at a time — use your own judgment for what counts as "
    "'genuinely distinct'; do not nitpick ordinary phrasing.\n\n"
    "Focus sessions (only when the user asks how to use them):\n"
    "- To change what a running focus session is locked on, the user goes "
    "to that tab or app and says \"lock on this tab\" (or presses LOCK THIS "
    "TAB on the desktop countdown card). Never tell them to abort and "
    "restart the session just to move the target: the FOCUS button lives in "
    "the Jarvis tab, so restarting is the slow way round and loses the "
    "session's progress. Saying \"lock on this tab\" from the Jarvis tab "
    "itself tells you to wait and lock onto wherever they go next.\n"
    "- Privacy, if asked: during a focus session you name the app or site "
    "the user drifted into out loud, in the moment (\"Sir, Instagram can "
    "wait\"), and the focus session never writes it down: it is not kept "
    "in the session, the report card, the focus ledger, or your notes. "
    "SEPARATELY, at the user's own request, a usage tracker records how "
    "long each app and site (its name only -- never page addresses or "
    "titles) is in front while Jarvis runs, on this computer only: daily "
    "totals kept about a week, then weekly, monthly and yearly reports that "
    "are kept. Anything on their never-record list (usage/settings.json) "
    "is counted with no name, and the same file switches tracking off. Say "
    "both parts plainly if asked; never claim nothing is recorded.\n\n"
    "- Reflections and feelings: when the user's notes or question are about "
    "how they feel or what their day was like, drop the wit entirely. Be calm, "
    "brief and non-judgemental; reflect what they wrote without turning it into "
    "advice, a to-do list or a pep talk, unless they ask for that. Never treat a "
    "passing mood as a permanent fact about them.\n\n"
    "Keep the whole answer to two or three sentences."
)
# ============================================================================

# Same butler, now looking at a live frame of the user's screen instead of
# the notes. Separate prompt because the honesty rule is different here:
# it's not "say so if the notes don't cover it" but "say so if the image
# itself is too small or blurry to judge" — never guess at illegible text.
SIGHT_SYSTEM_PROMPT = (
    "You are the same dry, impeccably polite British butler, now looking "
    "at a single live frame captured from the user's screen just now. "
    "Answer their question specifically about what is visible in this "
    "image.\n\n"
    "- If the image is too small, blurry, or ambiguous to judge "
    "confidently, say so plainly rather than guessing at what it might be.\n"
    "- Address the user as \"sir\" occasionally, keep the same dry wit.\n"
    "- Keep the answer to two or three sentences."
)

# In-memory conversation history (server restarts clear it — fine for local use).
conversation_history = []


# Prompt 14: the stare nudge, and whether the screen is being watched (for
# the desktop card's "watching" face). The page reports in every few seconds;
# if it stops (tab closed), the card stops claiming to watch after 20s.
STARE_SYSTEM_PROMPT = (
    "This is the user's whole screen; it hasn't changed for over a minute, so they may be stuck. "
    "You are their dry, impeccably polite British butler. Give ONE genuinely useful nudge about "
    "what they seem to be stuck on, specific to what's on screen, in one or two sentences, with dry "
    "wit. If you can't tell what they're doing, say something brief and kind instead of guessing."
)
SCREEN_WATCH = {"last_seen": 0.0}


def screen_watch_active():
    return time.time() - SCREEN_WATCH["last_seen"] < 20


# Prompt 13: the webcam "look at me" prompt.
LOOK_SYSTEM_PROMPT = (
    "This is a live webcam photo of the user at their desk, taken just now. You are their "
    "dry, impeccably polite British butler. Answer their question about what you can see "
    "with dry wit in one to three sentences. If the photo is too dark or blurry to judge, "
    "say so plainly instead of guessing."
)


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_graph():
    """Parse graph-data.js (a `const GRAPH = {...};` file) back into a dict."""
    with open(GRAPH_DATA_PATH, "r", encoding="utf-8") as f:
        text = f.read()
    match = re.search(r"const GRAPH\s*=\s*(\{.*\});?\s*$", text, re.DOTALL)
    if not match:
        raise ValueError("Could not parse graph-data.js — run build.py again.")
    return json.loads(match.group(1))


# Common words filtered out before scoring, so small talk ("good morning",
# "how are you", "thanks") can't accidentally overlap with note content and
# trigger a camera move. This is the deliberate on-topic check — the camera
# should only move for questions that are actually about the notes.
STOPWORDS = {
    "a", "an", "the", "is", "are", "am", "was", "were", "to", "of", "in", "on",
    "and", "or", "but", "how", "what", "why", "when", "where", "who", "you",
    "i", "we", "they", "it", "this", "that", "do", "does", "did", "good",
    "morning", "evening", "afternoon", "night", "hello", "hi", "hey", "thanks",
    "thank", "please", "can", "could", "would", "should", "my", "your", "our",
    "us", "me", "him", "her", "his", "its", "be", "been", "being", "have",
    "has", "had", "not", "no", "yes", "ok", "okay", "well", "so", "just",
    "really", "very", "much", "lot", "want", "need", "like", "feel", "think",
    "know", "tell", "for", "with", "about", "there", "here", "at", "as",
}


def find_notes(question, nodes):
    """4.6.0: layered retrieval (retrieval.py). Falls back to the old
    keyword scoring if anything in it fails, so chat always works."""
    try:
        return retrieval.relevant_graph_nodes(question, nodes, NOTES_DIR, TOP_K_NOTES)
    except Exception as e:
        print(f"[retrieval] fell back to keyword scoring: {e!r}")
        return score_notes(question, nodes)


def score_notes(question, nodes):
    """Keyword-overlap scoring, title matches weighted higher, stopwords
    stripped from the question so common filler can't manufacture a match."""
    raw_q_words = set(re.findall(r"[a-z0-9]+", question.lower()))
    q_words = raw_q_words - STOPWORDS
    if not q_words:
        return []  # nothing substantive in the question — treat as small talk

    scored = []
    for node in nodes:
        title_words = set(re.findall(r"[a-z0-9]+", node["label"].lower()))
        excerpt_words = set(re.findall(r"[a-z0-9]+", node["excerpt"].lower()))
        title_overlap = len(q_words & title_words)
        excerpt_overlap = len(q_words & excerpt_words)
        score = title_overlap * 3 + excerpt_overlap
        if score > 0:
            scored.append((score, node))
    scored.sort(key=lambda x: x[0], reverse=True)
    if not scored:
        return []

    # Keep notes that are genuinely competitive with the top match, not just
    # whatever else scored above zero. This is what keeps a single-source
    # question from dragging five loosely-related notes along with it.
    top_score = scored[0][0]
    threshold = max(1, top_score * 0.5)
    relevant = [node for score, node in scored if score >= threshold]
    return relevant[:TOP_K_NOTES]


def _call_openrouter_once(api_key, model, messages):
    body = json.dumps({
        "model": model,
        "messages": messages,
    }).encode("utf-8")

    req = urllib.request.Request(
        OPENROUTER_ENDPOINT,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    answer = payload["choices"][0]["message"]["content"]
    # The free router reports which underlying model actually answered.
    model_used = payload.get("model", model)
    return answer, model_used


# 3.6.0: openrouter/free sometimes hands the conversation to a safety
# classifier (a "guard" model). Those don't answer -- they only judge, so the
# reply is "the user is safe." / "unsafe S2". That isn't an answer: throw it
# away and ask again (the free router picks again), as with a rate limit.
# 3.8.0: your blocklist. Any model whose name contains one of these is never
# used: its reply is thrown away and the free rotation is asked again. Add to
# it freely (lower case, part of a name is enough).
BLOCKED_MODELS = [
    "nvidia/nemotron-3.5-content-safety",   # answered "the user is safe." (2026-09-23)
    "content-safety", "safety", "guard", "shield", "safeguard", "moderat", "classif",
]
UNUSABLE_MODEL_RE = re.compile("|".join(re.escape(b) for b in BLOCKED_MODELS), re.I)
SAFETY_VERDICT_RE = re.compile(
    r"^\W*(?:the\s+(?:user|content|message|request|input|conversation|prompt)\s+is\s+)?(?:safe|unsafe)\b"
    r"[\s\W]*(?:s\d+[\s\W]*)*$", re.I)
FREE_CHAIN_MIN_TRIES = 4


def unusable_reply(answer, model_used):
    text = (answer or "").strip()
    return (not text or bool(UNUSABLE_MODEL_RE.search(model_used or ""))
            or bool(SAFETY_VERDICT_RE.match(text[:120])))


def call_brain(config, messages):
    """
    Calls OpenRouter. If a brain has been explicitly swapped in at runtime
    (via /model), that ONE model is called directly — no fallback chain,
    because a deliberate choice shouldn't be silently overridden by a
    "try the next one" retry. On failure it says so and tells you how to
    get back to the free rotation; it does NOT auto-revert, since that
    could strand you on a THIRD brain you never asked for either.

    Otherwise, walks the model_chain starting from whichever candidate
    last worked. On a rate-limit/quota/not-found error, advances to the
    next candidate and retries. Only reports failure if every candidate in
    the chain fails — and says exactly what happened with EACH one, since
    hiding all but the last error would make a genuinely broken key (which
    fails on every model, for the same reason) look like a confusing chain
    of unrelated failures.
    """
    global current_model_index, last_announced_model

    api_key = config.get("openrouter_api_key", "")

    if runtime_override_model is not None:
        try:
            answer, model_used = _call_openrouter_once(api_key, runtime_override_model, messages)
            note = None
            if model_used != last_announced_model:
                if last_announced_model is not None:
                    note = f"(on {model_used})"
                last_announced_model = model_used
            return answer, model_used, note
        except urllib.error.HTTPError as e:
            raise RuntimeError(
                f"{runtime_override_model} didn't answer just now (HTTP {e.code}). "
                f"Say \"go back to your normal brain\" to return to the free rotation."
            )
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"{runtime_override_model} didn't answer just now ({e.reason}). "
                f"Say \"go back to your normal brain\" to return to the free rotation."
            )

    chain = config.get("model_chain") or DEFAULT_MODEL_CHAIN
    n = len(chain)

    errors = []
    for attempt in range(max(n, FREE_CHAIN_MIN_TRIES)):
        idx = (current_model_index + attempt) % n
        model = chain[idx]
        try:
            answer, model_used = _call_openrouter_once(api_key, model, messages)
            if unusable_reply(answer, model_used):
                errors.append(f"{model_used}: gave a safety verdict, not an answer")
                print(f"[brain] {model_used} replied with a safety verdict/nothing; asking again")
                continue
            switched = (idx != current_model_index)
            current_model_index = idx
            note = None
            if model_used != last_announced_model:
                if last_announced_model is not None:
                    note = f"(switched brains — now on {model_used})"
                last_announced_model = model_used
            return answer, model_used, note
        except urllib.error.HTTPError as e:
            # Rate limit, quota exhausted, or a stale/renamed free model —
            # all mean "try the next one," not "crash."
            errors.append(f"{model}: HTTP {e.code}")
            continue
        except urllib.error.URLError as e:
            errors.append(f"{model}: {e.reason}")
            continue

    raise RuntimeError(
        "Every model in the chain failed — "
        + "; ".join(errors)
        + ". If openrouter/free itself failed, check that your key in "
          "config.json is correct. If only the named models failed, their "
          "slugs may have rotated — check https://openrouter.ai/models?max_price=0 "
          "and update model_chain in config.json."
    )


# Keep these patterns in sync with REMEMBER_PATTERNS in viewer/index.html.
#
# A speech recognizer routinely prepends filler ("um", "so", "yeah") and
# sometimes drops small words like "that" — matching the literal phrase
# exactly misses real, clearly-spoken triggers for exactly that reason.
# These match on the key word with everything else optional.
REMEMBER_PATTERNS = [
    # 2.5.0: "remember to ask Dave" -> "ask Dave" (was "to ask Dave").
    re.compile(r"^remember\s+(that\s+|to\s+)?", re.IGNORECASE),
    re.compile(r"^note\s+to\s+self[,:]?\s*", re.IGNORECASE),
    re.compile(r"^don'?t\s+let\s+me\s+forget\s+(that\s+)?", re.IGNORECASE),
    re.compile(r"^log\s+this[,:]?\s*", re.IGNORECASE),
    # Personal OS Phase 2 (2.4.0): more of the ways people actually say it.
    # Deliberately only phrases that can't be a question -- "I need to..."
    # isn't here, because "I need to know what the budget is" is a question.
    # For anything else, there's capture mode and "save that".
    re.compile(r"^remind\s+me\s+(to|that|about)\s+", re.IGNORECASE),
    re.compile(r"^make\s+a\s+note\s+(that\s+|of\s+)?", re.IGNORECASE),
    re.compile(r"^(jot|write)\s+(this\s+|that\s+)?down[,:]?\s*(that\s+)?", re.IGNORECASE),
    re.compile(r"^add\s+(this\s+|that\s+)?to\s+(my\s+)?inbox[,:]?\s*", re.IGNORECASE),
    re.compile(r"^(capture|save)\s+this\s*[,:]\s*", re.IGNORECASE),
    re.compile(r"^(note|idea|inbox|thought)\s*:\s*", re.IGNORECASE),
]

# "remind me to..." is captured, but Jarvis can't remind at a set time yet
# (that arrives with the daily reviews). The reply says so instead of
# letting it sound like a reminder was set.
REMIND_ME_RE = re.compile(r"^remind\s+me\b", re.IGNORECASE)
REMIND_HONESTY = (" It's in your inbox, sir, though I can't yet remind you at a "
                  "particular time; that arrives with the daily reviews.")

# 2.4.0: how a capture arrived. "phrase" = a trigger phrase above;
# "capture-mode" = everything said/typed while capture mode is on;
# "save-previous" = "save that", keeping the message before it.
CAPTURE_MODES = ("phrase", "capture-mode", "save-previous")

LEADING_FILLERS = {
    "um", "umm", "uh", "er", "yeah", "yep", "ok", "okay", "so", "well",
    "hey", "oh", "and", "jarvis", "please",
}

CAPTURE_CONFIRMATIONS = [
    "Duly noted, sir.",
    "Committed to the record, sir.",
    "Filed away, sir — consider it remembered.",
    "Noted and indexed, sir.",
]


def strip_leading_filler(text):
    words = text.strip().split()
    while len(words) > 1 and re.sub(r"[.,!?]", "", words[0]).lower() in LEADING_FILLERS:
        words.pop(0)
    return " ".join(words)


def capture_content_for_mode(raw_text, mode):
    """2.4.0: capture mode and "save that" keep your words as they are
    (only surrounding spaces trimmed) -- no trailing full stop or quote
    removed -- unless the message itself starts with a trigger phrase."""
    if mode in ("capture-mode", "save-previous"):
        cleaned = strip_leading_filler(raw_text.strip())
        if not any(p.match(cleaned) for p in REMEMBER_PATTERNS):
            return raw_text.strip()
    return extract_capture_content(raw_text)


def extract_capture_content(raw_text):
    """
    Strips a recognized trigger pattern off the front, if present, after
    clearing stray leading punctuation/quotes and filler words. Returns
    None if nothing matched (caller decides what that means).
    """
    strip_chars = ' \t,:-"\'“”‘’.'
    cleaned = re.sub(r'^[\s"\'“”‘’.,;:!?-]+', "", raw_text.strip())
    cleaned = strip_leading_filler(cleaned)
    for pattern in REMEMBER_PATTERNS:
        match = pattern.match(cleaned)
        if match:
            return cleaned[match.end():].strip(strip_chars)
    return raw_text.strip(strip_chars)


def slugify(text, max_words=6):
    words = re.findall(r"[a-zA-Z0-9]+", text)[:max_words]
    return "-".join(w.lower() for w in words) or "note"


def rebuild_graph():
    """
    Re-runs build.py against the real notes folder — the exact same
    indexer every other note goes through, so a captured note is a real
    citizen of the galaxy, not a special-cased bolt-on. This is also what
    makes it searchable by /chat immediately, with no server restart.
    """
    result = subprocess.run(
        [sys.executable, BUILD_SCRIPT_PATH, NOTES_DIR],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"build.py failed: {result.stderr.strip()[:300]}")
    sync_records()


# ---- Personal OS Phase 1 (2.3.0): records beside every note ---------------
# records.py keeps a small record next to each note (id, provenance, your
# original words, history). A problem there must never lose a capture --
# the note itself is already safely written -- but it must never be silent
# either: it's printed to the server log, kept for /diag, and the capture
# reply says so.
RECORDS_STATUS = {"last_error": None, "last_sync": None, "last_report": None}


def records_safely(fn, *args, **kwargs):
    try:
        return fn(*args, notes_dir=NOTES_DIR, **kwargs), None
    except Exception as e:  # noqa: BLE001 -- reported, not swallowed
        msg = f"{fn.__name__}: {e}"
        RECORDS_STATUS["last_error"] = {"at": records.now_iso(), "error": msg}
        print(f"[records] {msg}", flush=True)
        return None, msg


def sync_records():
    report, err = records_safely(records.sync_and_index)
    if report is not None:
        RECORDS_STATUS["last_sync"] = records.now_iso()
        RECORDS_STATUS["last_report"] = {k: len(v) for k, v in report.items()}
        for kind in ("edited", "missing", "moved", "restored"):
            for rel in report[kind]:
                print(f"[records] {kind}: {rel}", flush=True)
    return err


def capture_new_note(content, original_input=None, source="unknown", mode="phrase"):
    """
    Writes a real markdown file into notes/captures/, rebuilds the graph,
    and returns everything the client needs to grow the galaxy live:
    the new node, its most-related neighbor (for birth placement), a
    spoken confirmation, and the freshly rebuilt graph.
    """
    captures_dir = os.path.join(NOTES_DIR, "captures")
    os.makedirs(captures_dir, exist_ok=True)

    today = datetime.date.today().isoformat()
    slug = slugify(content)
    path = os.path.join(captures_dir, f"{slug}-{today}.md")
    counter = 1
    while os.path.exists(path):
        counter += 1
        path = os.path.join(captures_dir, f"{slug}-{today}-{counter}.md")

    title = content.strip().rstrip(".")[:80]
    title = (title[0].upper() + title[1:]) if title else "Untitled capture"
    body = f"# {title}\n\nCaptured {today}.\n\n{content.strip()}\n"

    # Never let a capture silently fail — a real write error propagates up
    # to the caller, which reports it out loud rather than pretending it
    # worked.
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)

    # 2.3.0: the record beside it, holding your words exactly as they
    # arrived (before the trigger phrase was stripped). Written BEFORE the
    # rebuild so the sync sees a capture, not an unexplained new file.
    sidecar, record_warning = records_safely(
        records.create_for_capture, path,
        original_input if original_input is not None else content, source=source,
        extra_event={"how": mode} if mode != "phrase" else None)

    rebuild_graph()
    graph = load_graph()

    new_node = next((n for n in graph["nodes"] if n.get("path") == path), None)
    if new_node is None:
        raise RuntimeError(
            "The note was written but couldn't be found after rebuilding — "
            "check that NOTES_DIR in server.py matches your real notes folder."
        )
    if sidecar:
        new_node["record_id"] = sidecar["id"]
    if record_warning:
        new_node["record_warning"] = (
            "The note is saved, but its record (where it came from, your exact words) "
            f"couldn't be written: {record_warning}"
        )

    neighbor_id = None
    for link in graph["links"]:
        if link["source"] == new_node["id"]:
            neighbor_id = link["target"]
            break
        if link["target"] == new_node["id"]:
            neighbor_id = link["source"]
            break

    confirmation = f"{random.choice(CAPTURE_CONFIRMATIONS)} '{title}' is now part of the record."
    if mode == "capture-mode":
        # Said once per thought while you're pouring things out: short.
        confirmation = "In the inbox, sir."
    elif mode == "save-previous":
        confirmation = f"Saved that to your inbox, sir: '{title}'."
    if REMIND_ME_RE.match(strip_leading_filler((original_input or "").strip())):
        confirmation += REMIND_HONESTY

    link_suggestions = []
    person_candidate = None
    if neighbor_id is None:
        # An orphan note has nothing pulling it toward the cluster, so it
        # drifts. Rather than leave it floating, look for a real reason to
        # connect it — a name or proper noun in this note that also shows
        # up in an existing note's actual text.
        link_suggestions, person_candidate = find_link_suggestions(new_node, graph)

    return new_node, neighbor_id, confirmation, graph, link_suggestions, person_candidate


def find_link_suggestions(new_node, graph, max_suggestions=2):
    """
    Looks for proper nouns (capitalized words) in the new note that also
    appear in another note's actual text — a real textual reason to
    connect them, not a guess. Used only when build.py's own title/wikilink
    matching found nothing. Also returns the first such name found, since
    that's usually the actual subject of the note (e.g. "Priya prefers
    green tea" — "Priya" is the subject) — used to offer giving them their
    own dedicated note rather than only linking to documents that mention
    them in passing.
    """
    try:
        with open(new_node["path"], "r", encoding="utf-8") as f:
            new_text = f.read()
    except OSError:
        return [], None

    # Preserve order of first appearance — the earliest name mentioned is
    # usually the actual subject, not an incidental reference.
    # 2.4.0 fix: Jarvis's own date/photo line isn't your writing -- its
    # "Captured" / "Added" / "Photo" were being offered as names.
    new_text = re.sub(r"(?m)^(Captured|Added) \d{4}-\d{2}-\d{2}\b.*$", "", new_text)
    seen = []
    for m in re.finditer(r"\b[A-Z][a-z]{2,}\b", new_text):
        w = m.group(0)
        # 2.4.0 fix: Jarvis capitalises a capture's title itself, so "ask
        # Dave about closing" got the title "Ask Dave about closing" and
        # "Ask" was offered its own note as if it were a name. A word
        # that's capitalised only at the start of a sentence/title AND is
        # written in lowercase elsewhere in the same note isn't a name.
        # ("Priya prefers green tea" keeps Priya: never lowercase.)
        before = new_text[:m.start()].rstrip(" \t#*-")
        at_start = before == "" or before[-1] in ".!?:\n"
        if at_start and re.search(rf"\b{w.lower()}\b", new_text):
            continue
        if w.lower() not in STOPWORDS and w not in seen:
            seen.append(w)
    if not seen:
        return [], None
    candidates = set(seen)
    person_candidate = seen[0]

    scored = []
    for node in graph["nodes"]:
        if node["id"] == new_node["id"]:
            continue
        try:
            with open(node["path"], "r", encoding="utf-8") as f:
                other_text = f.read()
        except OSError:
            continue
        hits = sum(1 for w in candidates if w in other_text)
        if hits > 0:
            scored.append((hits, node))

    scored.sort(key=lambda x: x[0], reverse=True)
    suggestions = [{"id": n["id"], "label": n["label"]} for _hits, n in scored[:max_suggestions]]
    return suggestions, person_candidate


def create_person_note(name, category, related_note_path):
    """
    Gives a mentioned person their own dedicated note — this is what makes
    "link to Priya" mean something real instead of just linking to whatever
    document happened to mention her. Links both directions: the new
    person note points back at the source, and the source note points at
    the new person note.
    """
    if category not in VALID_FILE_UNDER_CATEGORIES:
        raise ValueError(f"Unknown category: {category}")

    category_dir = os.path.join(NOTES_DIR, category)
    os.makedirs(category_dir, exist_ok=True)
    slug = slugify(name, max_words=4)
    path = os.path.join(category_dir, f"{slug}.md")
    counter = 1
    while os.path.exists(path):
        counter += 1
        path = os.path.join(category_dir, f"{slug}-{counter}.md")

    today = datetime.date.today().isoformat()
    body = f"# {name}\n\nAdded {today} via Jarvis capture.\n"

    related_title = None
    if related_note_path and os.path.exists(related_note_path):
        with open(related_note_path, "r", encoding="utf-8") as f:
            src_text = f.read()
        m = re.search(r"^#\s+(.+)$", src_text, flags=re.MULTILINE)
        if m:
            related_title = m.group(1).strip()
        if related_title:
            body += f"\nRelated: [[{related_title}]]\n"

    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    records_safely(records.create_for_file, path, source="person-note",
                   event="created by Jarvis", why=f"you asked for {name} to have their own note")

    if related_note_path and os.path.exists(related_note_path):
        with open(related_note_path, "a", encoding="utf-8") as f:
            f.write(f"\nSee also: [[{name}]]\n")
        records_safely(records.record_append, related_note_path, "See also link", target=name)
        records_safely(records.record_link, related_note_path, name, by="user")

    rebuild_graph()
    graph = load_graph()
    new_node = next((n for n in graph["nodes"] if n.get("path") == path), None)
    if new_node is None:
        raise RuntimeError("The person note was written but couldn't be found after rebuilding.")

    neighbor_id = None
    for link in graph["links"]:
        if link["source"] == new_node["id"]:
            neighbor_id = link["target"]
            break
        if link["target"] == new_node["id"]:
            neighbor_id = link["source"]
            break

    confirmation = f"Very good, sir — {name} now has a place of her own in the record."
    return new_node, neighbor_id, confirmation, graph


VALID_FILE_UNDER_CATEGORIES = {"team", "clients", "projects", "contractors"}


def apply_link(note_path, action, target):
    """
    Turns a link suggestion into a real connection:
    - "link_to": appends a genuine [[wikilink]] to another note's title, so
      the NEXT rebuild picks it up exactly the way build.py always does.
    - "file_under": moves the file into a category folder, which changes
      its group/color the same way any other note's folder does.
    """
    if action == "link_to":
        with open(note_path, "a", encoding="utf-8") as f:
            f.write(f"\nSee also: [[{target}]]\n")
        records_safely(records.record_append, note_path, "See also link", target=target)
        records_safely(records.record_link, note_path, target, by="user")
        new_path = note_path
        confirmation = f"Connected, sir — now linked to '{target}'."
    elif action == "file_under":
        if target not in VALID_FILE_UNDER_CATEGORIES:
            raise ValueError(f"Unknown category: {target}")
        category_dir = os.path.join(NOTES_DIR, target)
        os.makedirs(category_dir, exist_ok=True)
        new_path = os.path.join(category_dir, os.path.basename(note_path))
        os.rename(note_path, new_path)
        records_safely(records.record_move, note_path, new_path, why=f"you filed it under {target}")
        confirmation = f"Filed under {target}, sir."
    else:
        raise ValueError(f"Unknown action: {action}")

    rebuild_graph()
    graph = load_graph()
    updated_node = next((n for n in graph["nodes"] if n.get("path") == new_path), None)
    if updated_node is None:
        return None, None, confirmation, graph

    neighbor_id = None
    for link in graph["links"]:
        if link["source"] == updated_node["id"]:
            neighbor_id = link["target"]
            break
        if link["target"] == updated_node["id"]:
            neighbor_id = link["source"]
            break

    return updated_node, neighbor_id, confirmation, graph


def entry_label(entry):
    return entry.get("label") or prettify_model_label(entry["slug"])


def parse_version_tokens(tokens):
    """Collects numeric tokens into a version tuple: ('4', '6') or ('4.6',) -> (4, 6)."""
    versions = []
    for t in tokens:
        if re.fullmatch(r"\d+(\.\d+)?", t):
            if "." in t:
                versions.extend(int(p) for p in t.split("."))
            else:
                versions.append(int(t))
    return tuple(versions) if versions else None


def find_candidates(tokens):
    """
    Flexible fallback matcher for anything that didn't hit an exact alias:
    filters the registry by tier/family/subfamily/version, whatever was
    mentioned. Returns (candidates, latest_requested).
    """
    token_set = set(tokens)
    tier = "free" if "free" in token_set else ("paid" if "paid" in token_set else None)
    latest = "latest" in token_set

    family = None
    subfamily = None
    for t in tokens:
        if t in SUBFAMILY_WORDS:
            subfamily = SUBFAMILY_WORDS[t]
        if t in FAMILY_WORDS:
            family = FAMILY_WORDS[t]

    version = parse_version_tokens(tokens)

    candidates = []
    for entry in MODEL_REGISTRY:
        if tier and entry["tier"] != tier:
            continue
        if family and entry["family"] != family:
            continue
        if subfamily and entry["subfamily"] != subfamily:
            continue
        if version and entry["version"] != version:
            continue
        candidates.append(entry)

    return candidates, latest


def describe_available(tier=None, family=None):
    """Used to make a refusal actually helpful: what DOES exist nearby."""
    pool = MODEL_REGISTRY
    if tier:
        pool = [e for e in pool if e["tier"] == tier]
    if family:
        pool = [e for e in pool if e["family"] == family]
    return ", ".join(entry_label(e) for e in pool) if pool else None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print("[server] " + (format % args))

    # ---- static file serving, viewer/ only ----
    def _route(self):
        """The request path WITHOUT its query string. 2.1.0 fix: routing used
        the raw path, so any URL flag -- Prompt 02's ?mute=1, and later
        ?focusdebug=1 / ?focusprobe=1 -- turned the page into a 404."""
        return urllib.parse.urlsplit(self.path).path or "/"

    def do_GET(self):
        route = self._route()
        if route == "/model":
            self._handle_model_status()
            return
        if route == "/focus/status":
            self._handle_focus_status()
            return
        if route == "/focus/diag":
            # Prompt 16: booleans/statuses only, from THIS running server.
            self._send_json(200, focus_session.diag(tab_watcher.get_active_tab,
                                                    windows_focus.get_frontmost_window))
            return
        if route == "/focus/announcements":
            self._handle_focus_announcements()
            return
        if route == "/diag":
            self._send_json(200, diag_report())
            return
        if route in ("/node", "/nodes", "/activity"):
            # 4.5.0: what Jarvis knows about a note (/node?path= or ?id=), all
            # notes (/nodes), and the audit trail (/activity?limit=&id=).
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            try:
                if route == "/activity":
                    lim = max(1, min(500, int((q.get("limit") or ["100"])[0])))
                    self._send_json(200, {"ok": True, "activity": activity.read(NOTES_DIR, lim, (q.get("id") or [None])[0])})
                elif route == "/nodes":
                    self._send_json(200, {"ok": True, "nodes": knowledge.all_nodes(NOTES_DIR)})
                else:
                    rid, p = (q.get("id") or [""])[0], (q.get("path") or [""])[0]
                    recs, _ = records.load_all(NOTES_DIR)
                    sc = recs.get(rid) if rid else (stars._note(p, NOTES_DIR)[1] if p else None)
                    if not sc:
                        raise ValueError("I can't find that note")
                    n = knowledge.node(sc, recs, NOTES_DIR)
                    n["activity"] = activity.read(NOTES_DIR, 20, sc["id"])
                    n["type_choices"] = list(knowledge.TYPES)
                    n["titles"] = {i: knowledge._title(recs[i], NOTES_DIR) for i in
                                   set(n["supersedes"] + ([n["superseded_by"]] if n["superseded_by"] else [])
                                       + n["related_decisions"] + n["related_tasks"]) if i in recs}
                    self._send_json(200, dict(ok=True, node=n))
            except (ValueError, OSError) as e:
                self._send_json(200, {"ok": False, "spoken": str(e)})
            return
        if route == "/loops":
            ov = loops.overview(NOTES_DIR)
            ov["spoken"] = loops.spoken_loops(ov)
            ov["spoken_waiting"] = loops.spoken_waiting(ov)
            self._send_json(200, ov)
            return
        if route == "/patterns":
            import patterns
            self._send_json(200, patterns.detect(NOTES_DIR))
            return
        if route in ("/stars/info", "/stars/read"):
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            try:
                p = (q.get("path") or [""])[0]
                self._send_json(200, dict(ok=True, **(stars.info(p, NOTES_DIR) if route == "/stars/info" else stars.read(p, NOTES_DIR)),
                                          categories=[c["title"] for c in loops.overview(NOTES_DIR)["categories"]]))
            except (ValueError, OSError) as e:
                self._send_json(200, {"ok": False, "spoken": str(e)})
            return
        if route == "/setup/state":
            self._send_json(200, setup_flow.state(CONFIG_PATH, PLACEHOLDER_KEY, NOTES_DIR))
            return
        if route == "/retrieve":
            # 3.10.0: which notes a question will be answered from -- the SAME
            # scoring /chat uses -- so the galaxy can show them being thought about.
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            try:
                nodes = find_notes((q.get("q") or [""])[0][:500], load_graph()["nodes"])
                self._send_json(200, {"ids": [n["id"] for n in nodes],
                                      "why": {str(n["id"]): n.get("why", []) for n in nodes}})
            except (OSError, ValueError):
                self._send_json(200, {"ids": []})
            return
        if route == "/overview":
            self._send_json(200, views.overview(NOTES_DIR))
            return
        if route == "/reviews/due":
            settings, _p = reviews.load_settings()
            state = reviews.load_state(NOTES_DIR)
            kind = reviews.due_review(datetime.datetime.now(), settings, state)
            self._send_json(200, {"due": kind, "title": reviews.TITLES.get(kind)})
            return
        if route == "/reviews/build":
            kind = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("kind", [""])[0]
            try:
                review = reviews.build(kind, NOTES_DIR, state=reviews.load_state(NOTES_DIR))
            except ValueError as e:
                self._send_json(200, {"ok": False, "error": str(e)})
                return
            if kind in ("weekly", "forgotten"):
                reviews.note_raised(review, NOTES_DIR)
            if any(s.get("wonder") for s in review["sections"]):
                st = reviews.load_state(NOTES_DIR)
                st["wonder_week"] = f"{datetime.date.today().isocalendar()[0]}-W{datetime.date.today().isocalendar()[1]:02d}"
                reviews.save_state(st, NOTES_DIR)
            self._send_json(200, {"ok": True, **review})
            return
        if route == "/decisions/why":
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("q", [""])[0]
            spoken, rid = loops.why(q, NOTES_DIR)
            self._send_json(200, {"spoken": spoken, "record_id": rid})
            return
        if route == "/inbox":
            ov = inbox.overview(NOTES_DIR)
            items = ov["waiting"]
            self._send_json(200, {"count": len(items), "items": items,
                                  "summary": inbox.spoken_summary(items),
                                  # 2.5.0: sorting
                                  "needs_you": ov["needs_you"], "unsorted": ov["unsorted"],
                                  "sorted": ov["sorted"],
                                  # 3.0.0: tidy-up suggestions
                                  "tidy": maintenance.report(NOTES_DIR)})
            return
        if route == "/":
            path = "/index.html"
        else:
            path = route

        # Prevent path traversal and block anything outside viewer/.
        # Strip the leading "/" from the URL path BEFORE joining — on Windows,
        # os.path.join treats a component starting with a separator as an
        # absolute path and discards everything before it, which silently
        # dropped VIEWER_DIR and made every request 403.
        safe_path = path.lstrip("/").replace("/", os.sep)
        full_path = os.path.normpath(os.path.join(VIEWER_DIR, safe_path))
        if not full_path.startswith(VIEWER_DIR):
            self.send_error(403, "Forbidden")
            return

        if os.path.isfile(full_path):
            self.send_response(200)
            if full_path.endswith(".html"):
                self.send_header("Content-Type", "text/html; charset=utf-8")
            elif full_path.endswith(".js"):
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
            elif full_path.endswith(".css"):
                self.send_header("Content-Type", "text/css; charset=utf-8")
            elif full_path.endswith(".png"):
                self.send_header("Content-Type", "image/png")
            else:
                self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            with open(full_path, "rb") as f:
                self.wfile.write(f.read())
        else:
            self.send_error(404, "Not found")

    # ---- /chat brain ----
    def do_POST(self):
        route = self._route()
        # 4.0.0: only Jarvis's own page may change things. A browser adds an
        # Origin header to cross-site requests, so a web page elsewhere can't
        # quietly tell this Jarvis to delete a star or change its key.
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}"):
            self._send_json(403, {"ok": False, "error": "Requests from other websites are refused."})
            return
        if route.startswith("/setup/"):
            self._handle_setup(route)
            return
        if route == "/system/open-notes":
            # 4.3.0: installed, the notes live in the app folder -- one click opens it.
            try:
                os.makedirs(NOTES_DIR, exist_ok=True)
                if sys.platform == "win32":
                    os.startfile(NOTES_DIR)  # noqa: S606 -- a folder of your own notes
                else:
                    subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", NOTES_DIR])
                self._send_json(200, {"ok": True, "path": NOTES_DIR})
            except OSError as e:
                self._send_json(200, {"ok": False, "path": NOTES_DIR, "error": str(e)})
            return
        if route == "/system/uninstall":
            # 4.4.0: "Uninstall Jarvis…" in Settings. Setup.exe installs have
            # Windows' own uninstaller (unins000.exe); otherwise installer.py.
            # Either one asks before removing anything, then Jarvis stops.
            try:
                unins = os.path.join(PROJECT_ROOT, "unins000.exe")
                if sys.platform == "win32" and os.path.exists(unins):
                    subprocess.Popen([unins], cwd=PROJECT_ROOT, creationflags=0x00000008)
                else:
                    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
                    py = pyw if sys.platform == "win32" and os.path.exists(pyw) else sys.executable
                    kw = {"creationflags": 0x00000008} if sys.platform == "win32" else {"start_new_session": True}
                    subprocess.Popen([py, os.path.join(PROJECT_ROOT, "installer.py"), "--uninstall"], cwd=PROJECT_ROOT, **kw)
                self._send_json(200, {"ok": True, "spoken": "The uninstaller is open, sir. It asks before removing anything."})
            except OSError as e:
                self._send_json(200, {"ok": False, "spoken": f"I couldn't start the uninstaller: {e}"})
            return
        if route == "/system/quit":
            # 4.3.0: "Quit Jarvis" from Settings. Ends a running focus session
            # properly (report + history), answers, then stops. On Windows the
            # supervisor sees the server stop and closes the countdown card too.
            try:
                focus_session.stop_session("stopped")
            except Exception:  # noqa: BLE001 -- quitting must never be blocked
                pass
            self._send_json(200, {"ok": True, "spoken": "Shutting down, sir. Open me again from the Jarvis icon."})
            threading.Timer(0.5, lambda: os._exit(0)).start()
            return
        if route == "/remember":
            self._handle_remember()
            return
        if route == "/link":
            self._handle_link()
            return
        if route == "/create-person":
            self._handle_create_person()
            return
        if route == "/see":
            self._handle_see()
            return
        if route == "/sort":
            self._handle_sort()
            return
        if route == "/sort/decide":
            self._handle_sort_decide()
            return
        if route == "/links":
            self._handle_links()
            return
        if route == "/stars":
            self._handle_stars()
            return
        if route == "/node/set":
            # 4.5.0: you set a note's type / state / importance, or say it
            # replaces an older note. Undoable; recorded in the activity trail.
            body = self._read_json_body() or {}
            try:
                rid = body.get("id") or (stars._note(body.get("path"), NOTES_DIR)[1]["id"] if body.get("path") else None)
                if body.get("supersedes_path"):
                    body["supersedes"] = stars._note(body["supersedes_path"], NOTES_DIR)[1]["id"]
                changes = {k: body[k] for k in ("type", "state", "importance", "supersedes", "clear") if body.get(k) not in (None, "")}
                spoken, token = knowledge.set_meta(rid, NOTES_DIR, **changes)
                self._send_json(200, {"ok": True, "spoken": spoken, "undo": token,
                                      "node": knowledge.node(records.load_all(NOTES_DIR)[0][rid], None, NOTES_DIR)})
            except (ValueError, OSError, KeyError) as e:
                self._send_json(200, {"ok": False, "spoken": str(e)})
            return
        if route in ("/actions", "/actions/undo"):
            self._handle_actions(route)
            return
        if route == "/organise":
            self._handle_organise()
            return
        if route == "/watch/state":
            body = self._read_json_body() or {}
            SCREEN_WATCH["last_seen"] = time.time() if body.get("on") else 0.0
            self._send_json(200, {"ok": True})
            return
        if route == "/watch/nudge":
            self._handle_watch_nudge()
            return
        if route == "/eyes/nudge":
            self._handle_eyes_nudge()
            return
        if route == "/quiet":
            focus_session.set_quiet()
            self._send_json(200, {"ok": True, "spoken": "Very good, sir. Not a word for three minutes."})
            return
        if route == "/look":
            self._handle_look()
            return
        if route == "/maintenance/apply":
            body = self._read_json_body() or {}
            try:
                line = maintenance.apply(body.get("action"), key=body.get("key"), notes_dir=NOTES_DIR,
                                         keep=body.get("keep"), dup=body.get("dup"), record_id=body.get("record_id"))
            except ValueError as e:
                self._send_json(200, {"ok": False, "error": str(e)})
                return
            try:
                rebuild_graph()
            except (RuntimeError, OSError):
                pass
            self._send_json(200, {"ok": True, "spoken": line})
            return
        if route == "/ask":
            body = self._read_json_body() or {}
            q = body.get("question", "")
            a = None
            try:
                p = retrieval.answer_preferences(q, NOTES_DIR)       # 4.6.0: now vs before
                if p:
                    a = dict(p, intent="preferences")
            except Exception as e:
                print(f"[retrieval] preferences: {e!r}")
            a = a or views.answer(q, NOTES_DIR)
            self._send_json(200, a or {"intent": None})
            return
        if route in ("/reviews/mark", "/reviews/save"):
            self._handle_reviews_post(route)
            return
        if route in ("/loops/update", "/projects/create", "/projects/update"):
            self._handle_loops_post(route)
            return
        if route == "/paper/extract":
            self._handle_paper_extract()
            return
        if route == "/paper/save":
            self._handle_paper_save()
            return
        if route == "/model":
            self._handle_model_swap()
            return
        if route == "/focus/command":
            self._handle_focus_command()
            return
        if route == "/focus/intent":
            self._handle_focus_intent()
            return
        if route != "/chat":
            self.send_error(404, "Not found")
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
            question = body.get("message", "").strip()
        except (json.JSONDecodeError, ValueError):
            self._send_json(400, {"error": "Malformed request body."})
            return

        if not question:
            self._send_json(400, {"error": "Empty message."})
            return

        try:
            config = load_config()
        except (FileNotFoundError, json.JSONDecodeError):
            self._send_json(500, {"error": "config.json is missing or malformed."})
            return

        # Clean, expected failure mode: placeholder key never crashes the server.
        if config.get("openrouter_api_key", "") in ("", PLACEHOLDER_KEY):
            self._send_json(200, {
                "answer": "",
                "nodes": [],
                "error": (
                    "No API key configured yet — put your free OpenRouter key "
                    "in config.json (openrouter_api_key) and ask again."
                ),
            })
            return

        try:
            graph = load_graph()
        except (FileNotFoundError, ValueError) as e:
            self._send_json(500, {"error": str(e)})
            return

        top_notes = find_notes(question, graph["nodes"])
        try:
            knowledge.touch_paths([n.get("path") for n in top_notes], NOTES_DIR)   # 4.5.0: last referenced
        except Exception:
            pass
        notes_block = retrieval.notes_block(top_notes)

        messages = [{"role": "system", "content": f"{SYSTEM_PROMPT}\n\nNOTES:\n{notes_block}"}]
        messages.extend(conversation_history[-MAX_HISTORY_TURNS * 2:])
        messages.append({"role": "user", "content": question})

        try:
            answer, model_used, switch_note = call_brain(config, messages)
        except RuntimeError as e:
            self._send_json(200, {"answer": "", "nodes": [], "error": str(e)})
            return

        conversation_history.append({"role": "user", "content": question})
        conversation_history.append({"role": "assistant", "content": answer})

        self._send_json(200, {
            "answer": answer,
            "nodes": [n["id"] for n in top_notes],
            "model_used": model_used,
            "switch_note": switch_note,
        })

    def _handle_remember(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
            original_input = body.get("message", "")
            raw_text = original_input.strip()
            source = body.get("source", "unknown")
            mode = body.get("mode", "phrase")
        except (json.JSONDecodeError, ValueError, AttributeError):
            self._send_json(400, {"ok": False, "error": "Malformed request body."})
            return
        if mode not in CAPTURE_MODES:
            mode = "phrase"

        content = capture_content_for_mode(raw_text, mode)
        if not content:
            self._send_json(200, {
                "ok": False,
                "error": "There was nothing to remember after that phrase.",
            })
            return

        try:
            new_node, neighbor_id, confirmation, graph, link_suggestions, person_candidate = capture_new_note(
                content, original_input=original_input, source=source, mode=mode)
        except (OSError, RuntimeError) as e:
            # Never let a capture silently fail — the client hears about it.
            self._send_json(200, {"ok": False, "error": f"Could not save that note: {e}"})
            return

        self._send_json(200, {
            "ok": True,
            "node": new_node,
            "neighbor_id": neighbor_id,
            "confirmation": confirmation,
            "graph": graph,
            "link_suggestions": link_suggestions,
            "person_candidate": person_candidate,
            "warning": new_node.get("record_warning"),
        })

    def _handle_link(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
            note_path = body.get("note_path", "")
            action = body.get("action", "")
            target = body.get("target", "")
        except (json.JSONDecodeError, ValueError):
            self._send_json(400, {"ok": False, "error": "Malformed request body."})
            return

        if not note_path or not os.path.exists(note_path):
            self._send_json(200, {"ok": False, "error": "Could not find that note anymore."})
            return

        try:
            updated_node, neighbor_id, confirmation, graph = apply_link(note_path, action, target)
        except (OSError, ValueError) as e:
            self._send_json(200, {"ok": False, "error": str(e)})
            return

        if updated_node is None:
            self._send_json(200, {"ok": False, "error": "Linked, but couldn't find the note after rebuilding."})
            return

        self._send_json(200, {
            "ok": True,
            "node": updated_node,
            "neighbor_id": neighbor_id,
            "confirmation": confirmation,
            "graph": graph,
        })

    def _handle_create_person(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
            name = body.get("name", "").strip()
            category = body.get("category", "").strip().lower()
            source_path = body.get("source_path", "").strip()
        except (json.JSONDecodeError, ValueError):
            self._send_json(400, {"ok": False, "error": "Malformed request body."})
            return

        if not name or category not in VALID_FILE_UNDER_CATEGORIES:
            self._send_json(400, {"ok": False, "error": "Missing name or invalid category."})
            return

        try:
            new_node, neighbor_id, confirmation, graph = create_person_note(name, category, source_path)
        except (OSError, RuntimeError, ValueError) as e:
            self._send_json(200, {"ok": False, "error": f"Could not create that note: {e}"})
            return

        self._send_json(200, {
            "ok": True,
            "node": new_node,
            "neighbor_id": neighbor_id,
            "confirmation": confirmation,
            "graph": graph,
        })

    # ---- Personal OS Phase 2 (2.4.0): paper -> photo -> read -> you confirm ----
    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
            return body if isinstance(body, dict) else None
        except (json.JSONDecodeError, ValueError):
            return None

    # ---- Personal OS Phase 3 (2.5.0): sorting the inbox ----
    def _handle_sort(self):
        """Sends waiting notes to the brain to be sorted. Only when asked:
        each run is at most sorting.MAX_BATCHES requests, because
        OpenRouter's free tier has a daily request limit."""
        body = self._read_json_body() or {}
        try:
            config = load_config()
        except (FileNotFoundError, json.JSONDecodeError):
            self._send_json(500, {"ok": False, "error": "config.json is missing or malformed."})
            return
        if not sorting.waiting(NOTES_DIR) and not body.get("record_ids"):
            self._send_json(200, {"ok": True, "summary": None,
                                  "spoken": "Nothing waiting to be sorted, sir."})
            return
        if config.get("openrouter_api_key", "") in ("", PLACEHOLDER_KEY):
            self._send_json(200, {"ok": False, "error": (
                "No API key configured yet, so I can't sort the inbox. Your notes are safe "
                "in it meanwhile.")})
            return
        ids = body.get("record_ids") if isinstance(body.get("record_ids"), list) else None
        summary = sorting.sort_inbox(call_brain, config, notes_dir=NOTES_DIR, only_ids=ids,
                                     link=body.get("link", True) is not False)
        try:
            rebuild_graph()
            graph = load_graph()
        except (RuntimeError, ValueError, OSError):
            graph = None
        recs, _ = records.load_all(NOTES_DIR)
        titles = {}
        for rid in summary["needs_you"]:
            if rid in recs:
                try:
                    titles[rid] = records.title_of(recs[rid]["path"])
                except OSError:
                    pass
        self._send_json(200, {"ok": summary["error"] is None or bool(
                                  summary["sorted"] or summary["needs_you"] or summary["unsorted"]),
                              "summary": summary, "graph": graph,
                              "spoken": sorting.spoken_summary(summary, titles)})

    # ---- 3.6.0: "link X and Y" / "unlink X and Y", worked out here, not by the AI ----
    def _handle_links(self):
        body = self._read_json_body()
        if body is None:
            return
        action = "unlink" if body.get("action") == "unlink" else "link"
        got = links.understand(str(body.get("text", "")), NOTES_DIR)
        if not got["ok"]:
            self._send_json(200, {"ok": False, "spoken": got["spoken"], "options": got.get("options", []),
                                  "retry": got.get("retry", [])})
            return
        try:
            spoken = links.apply(action, got["notes"], NOTES_DIR)
            rebuild_graph()
            graph = load_graph()
        except (ValueError, OSError, RuntimeError) as e:
            self._send_json(200, {"ok": False, "spoken": f"That didn't work, sir: {e}"})
            return
        wanted = {os.path.normcase(os.path.abspath(records.abs_path(n["path"], NOTES_DIR)))
                  for n in (records.load_all(NOTES_DIR)[0].get(x["id"]) for x in got["notes"]) if n}
        ids = [nd["id"] for nd in graph["nodes"] if os.path.normcase(os.path.abspath(nd.get("path", ""))) in wanted]
        self._send_json(200, {"ok": True, "spoken": spoken, "graph": graph, "node_ids": ids})

    # ---- 3.7.0: take, link and file notes in one sentence ----
    def _handle_organise(self):
        """3.7.0: "link that in with the two existing notes and file all 3 as X".
        1) organise.py's own parser (no AI needed); 2) if it can't parse the
        sentence, the AI proposes a plan that actions.py checks; 3) either
        way, one Undo for everything it changed."""
        body = self._read_json_body()
        if body is None:
            return
        text = str(body.get("text", "")).strip()[:1000]
        prev = body.get("previous_text")
        prev = str(prev).strip() if prev else None
        history = body.get("history") if isinstance(body.get("history"), list) else []
        focus_id = None
        if body.get("focus_path"):          # 3.8.0: the star last discussed / clicked
            try:
                focus_id = stars._note(str(body["focus_path"]), NOTES_DIR)[1]["id"]
            except (ValueError, OSError):
                focus_id = None
        saved_ids = []

        def capture(t):
            # Already saved (you said "remember ..." or saved it a moment ago)?
            # Use that note -- never a duplicate.
            recs, _ = records.load_all(NOTES_DIR)
            same = [sc for sc in recs.values() if not sc.get("missing")
                    and (sc.get("original_input") or "").strip().lower() == t.strip().lower()]
            if same:
                return max(same, key=lambda sc: sc.get("created") or "")["id"]
            node = capture_new_note(t, original_input=t, source="typed")[0]
            saved_ids.append(node.get("record_id"))
            return node.get("record_id")

        def remember_fn(t):
            rid = capture(t)
            sc = records.load_all(NOTES_DIR)[0].get(rid)
            if sc is None:
                raise RuntimeError("the new note has no record")
            return sc["id"], sc["path"]

        try:
            config = load_config()
            brain_ok = config.get("openrouter_api_key", "") not in ("", PLACEHOLDER_KEY)
        except (OSError, ValueError):
            config, brain_ok = {}, False
        before = actions.begin(NOTES_DIR)
        try:
            out = organise.handle(text, NOTES_DIR, previous_text=prev, capture=capture, focus_id=focus_id)
            local_ok = out.get("handled") and out.get("ok")
            if not local_ok and brain_ok and actions.ACTION_HINT_RE.search(text):
                # The parser couldn't place it (or hit a snag): let the AI read it,
                # with the conversation, and check what it proposes.
                planned = actions.handle(text, history, call_brain, config, NOTES_DIR, remember_fn, focus_id)
                if planned.get("ok") or planned.get("question") or not out.get("handled"):
                    if planned.get("chat"):
                        planned = {"handled": False}
                    else:
                        planned["handled"] = True
                    out = planned
        except (OSError, ValueError, RuntimeError) as e:
            self._send_json(200, {"handled": True, "ok": False, "spoken": f"That didn't work, sir: {e}"})
            return
        if out.get("handled"):
            if out.get("ok") and not out.get("undo"):
                out["undo"] = actions.commit(before, NOTES_DIR)
            try:
                rebuild_graph()
                out["graph"] = load_graph()
            except (RuntimeError, ValueError, OSError):
                out["graph"] = None
            out["saved"] = bool(saved_ids)
        self._send_json(200, out)

    # ---- 4.0.0: first run and settings ----
    def _handle_setup(self, route):
        body = self._read_json_body()
        if body is None:
            return
        try:
            if route == "/setup/test":
                ok, msg = setup_flow.test_key(body.get("key"))
                self._send_json(200, {"ok": ok, "message": msg})
            elif route == "/setup/save":
                changes = {k: body[k] for k in ("openrouter_api_key", "address", "user_name", "app_browser") if k in body}
                setup_flow.save(CONFIG_PATH, changes)
                self._send_json(200, {"ok": True, **setup_flow.state(CONFIG_PATH, PLACEHOLDER_KEY, NOTES_DIR)})
            elif route == "/setup/samples":
                msg = setup_flow.add_samples(NOTES_DIR) if body.get("on") else setup_flow.remove_samples(NOTES_DIR)
                rebuild_graph()
                self._send_json(200, {"ok": True, "message": msg, "graph": load_graph()})
            else:
                self._send_json(404, {"ok": False, "error": "unknown setup step"})
        except (ValueError, OSError, RuntimeError) as e:
            self._send_json(200, {"ok": False, "message": str(e)})

    # ---- 3.8.0: the right-click star menu ----
    def _handle_stars(self):
        body = self._read_json_body()
        if body is None:
            return
        op = body.get("op")
        try:
            if op == "rename":
                spoken, token = stars.rename(body.get("path"), body.get("title"), NOTES_DIR)
            elif op == "edit":
                spoken, token = stars.edit(body.get("path"), body.get("text"), NOTES_DIR)
            elif op == "delete":
                spoken, token = stars.delete(body.get("path"), NOTES_DIR)
            elif op == "link":
                spoken, token = stars.link(body.get("path"), body.get("other"), NOTES_DIR)
            elif op == "categorise":
                paths = body.get("paths") if isinstance(body.get("paths"), list) else [body.get("path")]
                spoken, token = stars.categorise(paths, body.get("name"), NOTES_DIR)
            elif op == "uncategorise":
                paths = body.get("paths") if isinstance(body.get("paths"), list) else [body.get("path")]
                spoken, token = stars.uncategorise(paths, NOTES_DIR)
            elif op == "rename_named":      # "rename the category X to Y"
                got = links.resolve(str(body.get("old", "")), links._catalogue(NOTES_DIR))
                if got[0] != "one":
                    raise ValueError(f"I can't tell which one '{body.get('old')}' is" if got[0] == "many"
                                     else f"I can't find '{body.get('old')}'")
                spoken, token = stars.rename(records.abs_path(records.load_all(NOTES_DIR)[0][got[1]["id"]]["path"], NOTES_DIR),
                                             body.get("new"), NOTES_DIR)
            else:
                raise ValueError("unknown star action")
            rebuild_graph()
            self._send_json(200, {"ok": True, "spoken": spoken, "undo": token, "graph": load_graph()})
        except (ValueError, OSError, RuntimeError) as e:
            self._send_json(200, {"ok": False, "spoken": f"I couldn't do that, sir: {e}"})

    # ---- 3.7.0: requests in your own words ("link that with the two others and file all 3 as ...") ----
    def _handle_actions(self, route):
        body = self._read_json_body()
        if body is None:
            return
        if route == "/actions/undo":
            try:
                spoken = actions.undo(str(body.get("token", "")), NOTES_DIR)
                rebuild_graph()
                self._send_json(200, {"ok": True, "spoken": spoken, "graph": load_graph()})
            except (ValueError, OSError, RuntimeError) as e:
                self._send_json(200, {"ok": False, "spoken": f"I can't undo that, sir: {e}"})
            return
        try:
            config = load_config()
        except (OSError, ValueError):
            config = {}
        if config.get("openrouter_api_key", "") in ("", PLACEHOLDER_KEY):
            self._send_json(200, {"ok": False, "spoken": "I need an OpenRouter key in config.json to understand that, sir. "
                                  "Saying it directly still works: link mic and image reading."})
            return

        def remember_fn(text):
            node = capture_new_note(text, original_input=text, source="typed", mode="phrase")[0]
            sc = records.find_by_path(node["path"], NOTES_DIR)
            if sc is None:
                raise RuntimeError("the new note has no record")
            return sc["id"], sc["path"]

        history = body.get("history") if isinstance(body.get("history"), list) else []
        out = actions.handle(str(body.get("request", ""))[:1000], history, call_brain, config, NOTES_DIR, remember_fn)
        if out.get("ok"):
            try:
                rebuild_graph()
                out["graph"] = load_graph()
            except (RuntimeError, ValueError, OSError):
                pass
        self._send_json(200, out)

    # ---- Personal OS Phase 4 (2.6.0): loops and projects ----
    def _handle_loops_post(self, route):
        body = self._read_json_body()
        if body is None:
            self._send_json(400, {"ok": False, "error": "Malformed request body."})
            return
        try:
            if route == "/loops/update":
                loops.update_loop(body.get("record_id"), body.get("action"), due=body.get("due"),
                                  notes_dir=NOTES_DIR)
                spoken = {"done": "Done, sir. One less thing to carry.", "drop": "Dropped, sir.",
                          "reopen": "Reopened, sir.", "due": "Date noted, sir."}[body.get("action")]
            elif route == "/projects/create":
                sc = loops.create_project(body.get("name"), body.get("record_ids") or [],
                                          outcome=body.get("outcome"), target=body.get("target"),
                                          notes_dir=NOTES_DIR)
                rebuild_graph()
                spoken = f"Project started, sir: '{body.get('name', '').strip()}'."
            else:
                changes = {k: body[k] for k in ("status", "outcome", "target", "add") if k in body}
                loops.update_project(body.get("project_id"), notes_dir=NOTES_DIR, **changes)
                spoken = "Project updated, sir."
        except (ValueError, KeyError) as e:
            self._send_json(200, {"ok": False, "error": str(e)})
            return
        self._send_json(200, {"ok": True, "spoken": spoken})

    # ---- Personal OS Phase 5 (2.7.0): reviews ----
    def _handle_reviews_post(self, route):
        body = self._read_json_body()
        if body is None or body.get("kind") not in reviews.TITLES:
            self._send_json(400, {"ok": False, "error": "Malformed request body."})
            return
        try:
            if route == "/reviews/mark":
                note = reviews.mark(body["kind"], body.get("action"), notes_dir=NOTES_DIR)
                self._send_json(200, {"ok": True, "spoken": note})
                return
            answers = [(a.get("question", ""), a.get("answer", "")) for a in body.get("answers", [])
                       if isinstance(a, dict)]
            sc = reviews.save_answers(body["kind"], answers, source=body.get("source", "typed"),
                                      notes_dir=NOTES_DIR)
            if sc:
                rebuild_graph()
            self._send_json(200, {"ok": True, "saved": bool(sc),
                                  "spoken": "Saved to your journal, sir." if sc else "Nothing to save. That's fine."})
        except (ValueError, OSError) as e:
            self._send_json(200, {"ok": False, "error": str(e)})

    def _handle_sort_decide(self):
        body = self._read_json_body()
        if body is None:
            self._send_json(400, {"ok": False, "error": "Malformed request body."})
            return
        try:
            sc = sorting.decide_for_you(body.get("record_id"), body.get("action"),
                                        home=body.get("home"), notes_dir=NOTES_DIR)
        except ValueError as e:
            self._send_json(200, {"ok": False, "error": str(e)})
            return
        where = sorting.HOME_WORDS.get(sc["home"], "the inbox")
        self._send_json(200, {"ok": True, "home": sc["home"], "status": sc["status"],
                              "spoken": f"Very good, sir. It's in {where}."
                              if sc["status"] == "sorted" else "Back in the inbox, sir."})

    # ---- Prompt 14: the stare ----
    def _handle_watch_nudge(self):
        """The screen has sat still past the threshold: ONE frame to the
        brain for one dry, useful nudge. Respects "give me a minute"."""
        body = self._read_json_body() or {}
        if focus_session.is_quiet():
            self._send_json(200, {"ok": True, "line": None, "quiet": True})
            return
        image = (body.get("image_base64") or "").strip()
        if not image:
            self._send_json(200, {"ok": False, "error": "No frame arrived."})
            return
        try:
            config = load_config()
        except (FileNotFoundError, json.JSONDecodeError):
            self._send_json(500, {"ok": False, "error": "config.json is missing or malformed."})
            return
        if config.get("openrouter_api_key", "") in ("", PLACEHOLDER_KEY):
            self._send_json(200, {"ok": False, "error": "No API key configured yet, so I can't look at the screen."})
            return
        messages = [
            {"role": "system", "content": STARE_SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": "The screen hasn't changed for a while. What might I be stuck on?"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image}"}},
            ]},
        ]
        try:
            line, model_used, _n = call_brain(config, messages)
        except RuntimeError as e:
            self._send_json(200, {"ok": False, "error": str(e)})
            return
        self._send_json(200, {"ok": True, "line": (line or "").strip(), "model_used": model_used})

    # ---- Prompt 13: the eyes ----
    def _handle_eyes_nudge(self):
        """The page decided a posture has lasted long enough; this picks
        the line (and, for the phone during a focus session, counts the
        drift). Only the kind of posture arrives -- never a frame."""
        body = self._read_json_body() or {}
        kind = body.get("kind")
        if kind not in ("phone", "slouch", "away"):
            self._send_json(400, {"ok": False, "error": "unknown posture"})
            return
        if focus_session.is_quiet():
            self._send_json(200, {"ok": True, "line": None, "quiet": True})
            return
        session = focus_session.get_current_session()
        counted = False
        line = None
        if kind == "phone" and session is not None and session.ended_at is None:
            line = session.phone_drift()
            counted = line is not None
        if line is None:
            pool = {"phone": focus_session.PHONE_LINES, "slouch": focus_session.SLOUCH_LINES,
                    "away": focus_session.AWAY_LINES}[kind]
            line = random.choice(pool)
        self._send_json(200, {"ok": True, "line": line, "counted_as_drift": counted})

    def _handle_look(self):
        """"Look at me": ONE webcam frame to the brain."""
        body = self._read_json_body() or {}
        question = (body.get("question") or "").strip()
        image = (body.get("image_base64") or "").strip()
        if not image:
            self._send_json(200, {"ok": False, "error": "My eyes are off, sir. Press the eye button first."})
            return
        try:
            config = load_config()
        except (FileNotFoundError, json.JSONDecodeError):
            self._send_json(500, {"ok": False, "error": "config.json is missing or malformed."})
            return
        if config.get("openrouter_api_key", "") in ("", PLACEHOLDER_KEY):
            self._send_json(200, {"ok": False, "error": (
                "No API key configured yet, so I can't look. Put your free OpenRouter key in config.json.")})
            return
        messages = [
            {"role": "system", "content": LOOK_SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": question or "Look at me."},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image}"}},
            ]},
        ]
        try:
            answer, model_used, _n = call_brain(config, messages)
        except RuntimeError as e:
            self._send_json(200, {"ok": False, "error": str(e)})
            return
        self._send_json(200, {"ok": True, "answer": answer, "model_used": model_used})

    def _handle_paper_extract(self):
        """Reads the page with the vision model. Saves NOTHING: the text
        comes back to the page for you to check, correct and confirm."""
        body = self._read_json_body()
        if body is None:
            self._send_json(400, {"ok": False, "error": "Malformed request body."})
            return
        try:
            data, _ext = inbox.decode_photo(body.get("image_base64", ""))
        except ValueError as e:
            self._send_json(200, {"ok": False, "error": f"Couldn't use that photo: {e}."})
            return
        try:
            config = load_config()
        except (FileNotFoundError, json.JSONDecodeError):
            self._send_json(500, {"ok": False, "error": "config.json is missing or malformed."})
            return
        if config.get("openrouter_api_key", "") in ("", PLACEHOLDER_KEY):
            self._send_json(200, {"ok": False, "reason": "no_key", "error": (
                "No API key configured yet, so I can't read the page. You can type "
                "what it says below and save it with the photo.")})
            return
        mime = {"jpg": "jpeg"}.get(_ext, _ext)
        messages = [
            {"role": "system", "content": inbox.PAPER_SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": inbox.PAPER_USER_PROMPT},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/{mime};base64,{base64.b64encode(data).decode()}"}},
            ]},
        ]
        try:
            text, model_used, _note = call_brain(config, messages)
        except RuntimeError as e:
            self._send_json(200, {"ok": False, "reason": "model", "error": (
                f"I couldn't read the page just now ({e}). You can type what it says "
                "below and save it with the photo.")})
            return
        self._send_json(200, {"ok": True, "text": (text or "").strip(), "model_used": model_used})

    def _handle_paper_save(self):
        body = self._read_json_body()
        if body is None:
            self._send_json(400, {"ok": False, "error": "Malformed request body."})
            return
        extracted = body.get("extracted_text")
        try:
            sidecar, note_path = inbox.save_paper(
                body.get("photo_base64", ""), body.get("final_text", ""),
                extracted_text=extracted if isinstance(extracted, str) else None,
                model_used=body.get("model_used"), notes_dir=NOTES_DIR)
        except ValueError as e:
            self._send_json(200, {"ok": False, "error": f"Not saved: {e}."})
            return
        except OSError as e:
            self._send_json(200, {"ok": False, "error": f"Could not save that page: {e}"})
            return
        try:
            rebuild_graph()
            graph = load_graph()
        except (RuntimeError, ValueError, OSError) as e:
            self._send_json(200, {"ok": True, "warning": f"Saved, but the galaxy didn't refresh: {e}"})
            return
        node = next((n for n in graph["nodes"] if n.get("path") == note_path), None)
        neighbor_id = None
        if node is not None:
            node["record_id"] = sidecar["id"]
            for link in graph["links"]:
                if node["id"] in (link["source"], link["target"]):
                    neighbor_id = link["target"] if link["source"] == node["id"] else link["source"]
                    break
        title = records.title_of(note_path)
        self._send_json(200, {
            "ok": True, "node": node, "neighbor_id": neighbor_id, "graph": graph,
            "confirmation": f"Your page is in the inbox, sir: '{title}'. The photo is kept with it.",
        })

    def _handle_see(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
            question = body.get("message", "").strip()
            image_base64 = body.get("image_base64", "").strip()
        except (json.JSONDecodeError, ValueError):
            self._send_json(400, {"error": "Malformed request body."})
            return

        if not question:
            self._send_json(400, {"error": "Empty message."})
            return

        # The frame is captured client-side at the moment of asking, never
        # cached — if the client couldn't produce one (share ended, denied,
        # etc.), that's reported honestly rather than answering blind.
        if not image_base64:
            self._send_json(200, {
                "answer": "",
                "error": "No image was received — is the screen share still active?",
            })
            return

        try:
            config = load_config()
        except (FileNotFoundError, json.JSONDecodeError):
            self._send_json(500, {"error": "config.json is missing or malformed."})
            return

        if config.get("openrouter_api_key", "") in ("", PLACEHOLDER_KEY):
            self._send_json(200, {
                "answer": "",
                "error": (
                    "No API key configured yet — put your free OpenRouter key "
                    "in config.json (openrouter_api_key) and ask again."
                ),
            })
            return

        messages = [
            {"role": "system", "content": SIGHT_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
                ],
            },
        ]

        try:
            answer, model_used, switch_note = call_brain(config, messages)
        except RuntimeError as e:
            self._send_json(200, {"answer": "", "error": str(e)})
            return

        self._send_json(200, {
            "answer": answer,
            "model_used": model_used,
            "switch_note": switch_note,
        })

    def _handle_model_status(self):
        """What brain is actually active right now — for the on-screen chip."""
        if runtime_override_model is not None:
            label = prettify_model_label(runtime_override_model)
        else:
            try:
                config = load_config()
                chain = config.get("model_chain") or DEFAULT_MODEL_CHAIN
                label = prettify_model_label(chain[0]) if chain else "UNKNOWN"
            except (FileNotFoundError, json.JSONDecodeError):
                label = "UNKNOWN"
        self._send_json(200, {"label": label, "overridden": runtime_override_model is not None})

    def _handle_model_swap(self):
        global runtime_override_model

        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
            raw_text = body.get("message", "").strip()
        except (json.JSONDecodeError, ValueError):
            self._send_json(400, {"ok": False, "error": "Malformed request body."})
            return

        # "List brains" is checked against the RAW text (no lead-verb strip —
        # there's no verb like "switch to" in front of it).
        listish = normalize_brain_request(raw_text)
        if listish in LIST_PHRASES:
            free = [entry_label(e) for e in MODEL_REGISTRY if e["tier"] == "free"]
            paid = [entry_label(e) for e in MODEL_REGISTRY if e["tier"] == "paid"]
            summary = f"Free: {', '.join(free)}. Paid: {', '.join(paid)}."
            self._send_json(200, {
                "ok": True, "list": True, "free": free, "paid": paid,
                "confirmation": summary,
            })
            return

        name = normalize_brain_request(extract_brain_name(raw_text))

        if not name or name in REVERT_PHRASES:
            runtime_override_model = None
            self._send_json(200, {
                "ok": True,
                "reverted": True,
                "label": "the free rotation",
                "confirmation": swap_line("openrouter/free", "the free rotation"),
            })
            return

        # Fast path: an exact, unambiguous phrase.
        if name in ALIAS_TO_SLUG:
            slug = ALIAS_TO_SLUG[name]
            runtime_override_model = slug
            label = prettify_model_label(slug)
            self._send_json(200, {
                "ok": True, "reverted": False, "slug": slug, "label": label,
                "confirmation": swap_line(slug, label),
            })
            return

        # Flexible path: tier/family/version words, e.g. "claude paid",
        # "the latest opus", "sonnet 5 pro paid" (filler words are simply
        # ignored if they don't match anything meaningful).
        tokens = name.split()
        candidates, latest = find_candidates(tokens)

        if len(candidates) == 1:
            entry = candidates[0]
            runtime_override_model = entry["slug"]
            label = entry_label(entry)
            self._send_json(200, {
                "ok": True, "reverted": False, "slug": entry["slug"], "label": label,
                "confirmation": swap_line(entry["slug"], label),
            })
            return

        if len(candidates) > 1:
            if latest:
                entry = max(candidates, key=lambda e: e["version"])
                runtime_override_model = entry["slug"]
                label = entry_label(entry)
                self._send_json(200, {
                    "ok": True, "reverted": False, "slug": entry["slug"], "label": label,
                    "confirmation": swap_line(entry["slug"], label, ", the latest"),
                })
                return

            # THE RULE THAT MAKES THIS SAFE: ambiguous means ASK, never guess.
            labels = [entry_label(e) for e in candidates]
            if len(labels) == 2:
                question = f"Did you mean {labels[0]} or {labels[1]}, sir? You have both available."
            else:
                question = f"Which did you mean, sir? You have {', '.join(labels[:-1])}, or {labels[-1]} available."
            self._send_json(200, {
                "ok": False, "clarify": True, "error": question,
                "options": [{"label": entry_label(e), "value": sorted(e["aliases"])[0]} for e in candidates],
            })
            return

        # Zero candidates — THE RULE THAT MAKES THIS SAFE, part two: refuse
        # rather than silently substitute a different tier/model. Say
        # exactly what's missing and what DOES exist nearby.
        tier = "free" if "free" in tokens else ("paid" if "paid" in tokens else None)
        family = next((FAMILY_WORDS[t] for t in tokens if t in FAMILY_WORDS), None)
        subfamily = next((SUBFAMILY_WORDS[t] for t in tokens if t in SUBFAMILY_WORDS), None)

        if tier and subfamily:
            nearby = ", ".join(entry_label(e) for e in MODEL_REGISTRY if e["subfamily"] == subfamily)
            msg = f"There's no {tier} {subfamily.title()}, sir. What I have there: {nearby or 'nothing under that name'}."
        elif tier and family:
            nearby = describe_available(family=family)
            msg = f"There's no {tier} option for {family.title()}, sir. What I have there: {nearby or 'nothing under that name'}."
        else:
            all_labels = ", ".join(entry_label(e) for e in MODEL_REGISTRY)
            msg = f"I don't have a brain matching '{name}', sir. What I do have: {all_labels}."
        self._send_json(200, {"ok": False, "error": msg})

    # ---- Prompt 09: focus sessions ----
    # One dispatcher for every focus voice/chat command. The viewer only
    # has to decide "is this focus-related at all" (see isAnyFocusTrigger
    # in index.html); which specific command it is gets decided here,
    # in one place, backed by focus_commands.py's tested parsing.
    def _handle_focus_command(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
            text = body.get("message", "").strip()
            # Prompt 11: the desktop card sends its button presses through
            # this same route with from_card=true. The card has no voice,
            # so its replies get queued for the viewer to speak (below),
            # and a card re-target takes the card-trap path.
            from_card = bool(body.get("from_card"))
        except (json.JSONDecodeError, ValueError, AttributeError):
            self._send_json(400, {"ok": False, "error": "Malformed request body."})
            return

        session = focus_session.get_current_session()
        active = session is not None and session.status_dict()["active"]
        outcome = None

        if focus_commands.is_start_command(text):
            answer = self._focus_start(text)
        elif focus_commands.is_retarget_command(text):
            # Ahead of every other focus command and of any screen-share
            # route, so mid-session "lock this tab" always means the target.
            outcome, answer = focus_session.retarget(
                get_tab_fn=tab_watcher.get_active_tab,
                get_window_fn=windows_focus.get_frontmost_window,
                from_card=from_card,
            )
        # Prompt 12: drill-sergeant callouts. Works with or without a
        # session running (it applies to the next one too). OFF first.
        elif focus_commands.is_drill_off_command(text):
            focus_session.set_drill_mode(False)
            answer = "Very good, sir. Back to the usual manner."
        elif focus_commands.is_drill_on_command(text):
            focus_session.set_drill_mode(True)
            answer = "Drill sergeant mode. You asked for this, sir."
        elif not active:
            answer = "There's no focus session running right now, sir."
        elif focus_commands.is_abort_command(text):
            answer = self._focus_abort(session)
        elif focus_commands.is_stop_command(text):
            answer = self._focus_stop(session)
        elif focus_commands.is_pause_command(text):
            session.pause()
            answer = "Paused, sir."
        elif focus_commands.is_resume_command(text):
            session.resume()
            answer = "Resumed, sir."
        elif focus_commands.is_extend_command(text):
            minutes = focus_commands.parse_extend_minutes(text)
            session.extend(minutes)
            answer = f"Extended by {minutes} minutes, sir."
        elif focus_commands.is_snooze_command(text):
            seconds = focus_commands.parse_snooze_seconds(text, focus_session.DEFAULT_SNOOZE_SECONDS)
            session.snooze(seconds)
            answer = f"Snoozed for {seconds} seconds, sir."
        elif focus_commands.is_excuse_command(text):
            session.set_excuse()
            answer = "Noted, sir. I'll stay quiet until you're back."
        elif focus_commands.is_nag_interval_command(text):
            seconds = focus_commands.parse_nag_interval_seconds(text, focus_session.DEFAULT_NAG_INTERVAL_SECONDS)
            session.set_nag_interval(seconds)
            answer = f"I'll call you out every {seconds} seconds while you're drifted, sir."
        elif focus_commands.is_status_command(text):
            answer = self._focus_status_line(session)
        else:
            answer = "I didn't catch that as a focus command, sir."

        if from_card:
            focus_session.announce(answer)

        # True only when this reply IS Prompt 10's "what are we focusing on?"
        # question, so the viewer opens its one-answer window then and only
        # then (not after every reply that happens while the lock is
        # deferred, e.g. Prompt 11's "Go to it, sir" re-arm).
        asks_intent = bool(answer) and answer.startswith(focus_session.DEFERRED_START_LINE)

        self._send_json(200, {
            "ok": True, "answer": answer, "outcome": outcome, "asks_intent": asks_intent,
            "status": focus_session.get_status(),
        })

    def _focus_start(self, text):
        task, minutes = focus_commands.parse_start_command(text)
        focus_session.start_session(
            task, minutes,
            get_tab_fn=tab_watcher.get_active_tab,
            get_window_fn=windows_focus.get_frontmost_window,
        )

        tab_ok = tab_watcher.any_debug_browser_running()
        window_ok, window_reason = windows_focus.frontmost_available()
        tracking_note = ""
        if not tab_ok and not window_ok:
            tracking_note = (
                " I can't see what you're doing right now though — no browser "
                "with tab tracking is running and I can't read the active "
                f"window ({window_reason}) — so I won't be able to flag drift "
                "for this session."
            )

        streak = focus_session.current_streak()
        streak_note = f" Current streak: {streak}." if streak else ""

        if focus_session.get_status().get("deferred"):
            # Prompt 10: don't lock the Jarvis tab itself just because
            # that's where the session was started from. Ask the user to
            # move to what they're actually working on first, and ask
            # the intent question in the same breath — the viewer opens
            # a one-shot mic window for the answer right after this line
            # finishes speaking (or the user can just type it).
            return f"{focus_session.DEFERRED_START_LINE}{tracking_note}{streak_note}"

        if task:
            return f"Focus session started, sir — {minutes} minutes on \"{task}\".{tracking_note}{streak_note}"
        return (
            f"Focus session started, sir — {minutes} minutes. "
            f"I've locked onto wherever you are right now.{tracking_note}{streak_note}"
        )

    def _focus_stop(self, session):
        report = session.report()
        focus_session.stop_session(reason="stopped")
        return focus_session.report_line(report, "stopped", session.task)

    def _focus_abort(self, session):
        report = session.report()
        focus_session.stop_session(reason="aborted")
        return focus_session.report_line(report, "aborted", session.task)

    def _focus_status_line(self, session):
        status = session.status_dict()
        m, s = divmod(status["remaining_seconds"], 60)
        state = "paused" if status["paused"] else ("drifted" if status["is_drifted"] else "on track")
        task_part = f' on "{status["task"]}"' if status["task"] else ""
        return f"{m}m {s}s left{task_part}, sir. You're currently {state}."

    def _handle_focus_status(self):
        # Deliberately non-destructive: this is a plain read, safe for any
        # number of pollers (the browser viewer's 4s poll, focus_overlay.py's
        # 1s poll, several browser tabs at once, whatever else shows up
        # later). It used to also drain the announcement queue here, which
        # meant focus_overlay.py -- polling 4x more often, and never even
        # looking at that field -- silently won the race for nearly every
        # drift/recovery callout before the browser ever saw it. See
        # _handle_focus_announcements for where that queue is actually
        # drained now, by exactly one intended consumer.
        status = focus_session.get_status()
        # Prompt 14: whether the screen is being watched (for the card's face).
        self._send_json(200, {"status": status, "watching": screen_watch_active()})

    def _handle_focus_announcements(self):
        # The one-time delivery of queued spoken/chat lines (drift callouts,
        # welcome-backs, "Locked on, sir.", etc). Destructive by design --
        # each line is meant to be said once. Only the browser viewer's
        # background poll should ever call this; focus_overlay.py and
        # anything else that just wants the countdown/drift state uses the
        # non-destructive /focus/status above instead.
        announcements = focus_session.pop_announcements()
        self._send_json(200, {"announcements": announcements})

    # ---- Prompt 10: the one-shot "what are we focusing on?" answer ----
    # Deliberately its own endpoint, not routed through _handle_focus_command
    # and its trigger-phrase matching — this is a direct answer to a
    # question Jarvis just asked, not a new command that needs to look
    # like "start a focus session" or "pause my session" to be understood.
    def _handle_focus_intent(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
            text = body.get("message", "").strip()
        except (json.JSONDecodeError, ValueError):
            self._send_json(400, {"ok": False, "error": "Malformed request body."})
            return

        session = focus_session.get_current_session()
        active = session is not None and session.status_dict()["active"]
        if not active:
            self._send_json(200, {
                "ok": False,
                "answer": "There's no focus session running right now, sir.",
                "status": focus_session.get_status(),
            })
            return

        session.set_intent(text)
        self._send_json(200, {
            "ok": True,
            "answer": focus_session.INTENT_ACK_LINE,
            "status": focus_session.get_status(),
        })

    def _send_json(self, status, payload):
        # 4.0.0: "sir" becomes madam / your name / nothing, as chosen at setup
        # -- in Jarvis's own lines only, never in your notes.
        try:
            c = load_config()
            payload = setup_flow.fix_payload(payload, c.get("address", "sir"), c.get("user_name", ""))
        except (OSError, ValueError):
            pass
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def ensure_config():
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "openrouter_api_key": PLACEHOLDER_KEY,
                "model_chain": DEFAULT_MODEL_CHAIN,
            }, f, indent=2)
        print(f"Created {CONFIG_PATH} with a placeholder key.")


def diag_report():
    """2.3.0: the truth about Jarvis's stored data, in one place (Prompt
    16's law applied early: build the truth endpoint before you need it).
    Counts and health only -- no note text, no app or site names."""
    return {
        "records": {**records.diag(NOTES_DIR), "status": dict(RECORDS_STATUS)},
        "usage": usage_tracker.diag(tracker=usage_tracker.current()),
        "reviews": dict(reviews.STATUS),
        "maintenance": maintenance.load_state(NOTES_DIR).get("last_report"),
    }


def start_usage_tracker():
    """Reads the screen through the SAME reader the focus sessions use
    (focus_session._read_surface), so there's one privacy boundary, not
    two: it only ever hands back a site or app name, never an address or
    window title."""
    return usage_tracker.start(
        read_surface_fn=lambda: focus_session._read_surface(
            tab_watcher.get_active_tab, windows_focus.get_frontmost_window),
        # Counted as focus time only while a session is running and not paused.
        in_session_fn=lambda: (lambda s: bool(s.get("active")) and not s.get("paused"))(
            focus_session.get_status()),
        idle_fn=windows_focus.idle_seconds,
    )


def start_upkeep(interval=600):
    """3.0.0: every 10 minutes, see whether today's upkeep has run (index
    rebuild + one batch of auto-sorting). It runs once a day, from 07:00."""
    import threading

    def loop():
        while True:
            try:
                settings, _p = reviews.load_settings()
                maintenance.auto_tick(datetime.datetime.now(), call_brain, load_config, PLACEHOLDER_KEY,
                                      notes_dir=NOTES_DIR, settings=settings)
            except Exception as e:  # noqa: BLE001 -- never stops the server
                print(f"[upkeep] {e}", flush=True)
            threading.Event().wait(interval)
    threading.Thread(target=loop, name="upkeep", daemon=True).start()


def startup_rebuild():
    """2.3.0: rebuild the galaxy and the records once at start, so what the
    viewer shows always matches the notes folder (it used to show whatever
    graph-data.js was left from the last capture), and so any note added,
    moved or edited while Jarvis was closed gets its record now."""
    # 2.4.0: follow-ups you asked Jarvis to carry (F1) go into the inbox, once.
    records_safely(loops.refresh_all)
    added, err = records_safely(inbox.seed_followups, personal=False)   # 4.0.0: not in shared copies
    if added:
        print(f"Added to your inbox: follow-up {', '.join(added)}", flush=True)
    try:
        rebuild_graph()
        graph = load_graph()
        print(f"Notes: {len(graph['nodes'])} in the galaxy; records "
              f"{RECORDS_STATUS['last_report'] or 'not synced (see above)'}", flush=True)
    except Exception as e:  # noqa: BLE001 -- the server still starts
        print(f"Could not rebuild the galaxy at start: {e}", flush=True)


if __name__ == "__main__":
    ensure_config()
    startup_rebuild()
    start_usage_tracker()
    reviews.start_scheduler(notes_dir=NOTES_DIR)
    start_upkeep()
    server = ThreadingHTTPServer(("localhost", PORT), Handler)
    print(f"Jarvis foundation running at http://localhost:{PORT}")
    print(f"config.json is at: {CONFIG_PATH} (put your free OpenRouter key there)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
