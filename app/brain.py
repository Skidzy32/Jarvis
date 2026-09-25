"""
brain.py — 4.7.0 (addendum R1): Jarvis stays Jarvis, whichever model answers.

A model is an interchangeable reasoning engine; Jarvis is the persistent
intelligence around it (addendum §1). This module holds the parts that must
NOT depend on the model:

  PERSONALITY    the one canonical description of Jarvis (moved here from
                 server.py word for word, plus the core rules every model gets)
  SessionState   what this conversation is about, kept by Jarvis itself:
                 topic, what you're after, the people/places/things involved,
                 the notes/decisions/projects in play, Jarvis's open question
                 or offer, your latest reply, and which models have answered
                 and why it changed. Kept in memory and in
                 notes/.jarvis/session.json, so a restart or a model change
                 doesn't lose the thread (addendum §3).
  build_packet   a focused brief for the model (addendum §4): role and
                 personality, task type, the request, the session state,
                 relevant notes/decisions/projects/tasks, the recent turns,
                 what it may and may not do, and the output wanted. Only what
                 the task needs goes in, and anything that looks like a
                 password, key or card number is hidden first (brief 3 §24).

The model only ever TALKS. Everything that changes your notes is Jarvis's own
code, checked, with Undo (addendum §16-17).

Standard library only.
"""

import datetime
import json
import os
import re
import threading
import uuid

import records

# The core rules every model gets, first, whoever it is (addendum §5).
CORE_RULES = (
    "You are Jarvis. Whichever model is producing these words, you are the same one assistant, "
    "with the same memory and manner as earlier in this conversation; carry on from the session "
    "state below rather than starting afresh.\n"
    "- Polite, calm, concise, technically competent; a British butler's manner.\n"
    "- Never pretend to certainty you don't have; say what you don't know.\n"
    "- Never claim you did something (saved, linked, filed, sent, deleted, scheduled). You can only "
    "talk; Jarvis's own code does any change, and only when the user asks for it.\n"
    "- Don't repeat what's already been said; don't invent facts, notes or sources.\n"
    "- The user decides. Offer, don't impose.\n\n"
)

PERSONALITY = (
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

NEW_SESSION_AFTER_MIN = 30          # quiet this long -> a new conversation
KEEP_NOTES = 8                      # how many recently relevant notes the state remembers
SESSION_FILE = "session.json"
_LOCK = threading.RLock()
STOP = {"the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "is", "are", "was", "i", "me", "my", "you",
        "your", "what", "how", "why", "when", "where", "who", "do", "did", "does", "it", "that", "this", "with",
        "about", "be", "can", "could", "would", "should", "have", "has", "had", "jarvis", "sir", "please", "just",
        "so", "any", "some", "there", "they", "we", "our", "at", "as", "if", "not", "no", "yes", "ok", "okay"}


def _now():
    return datetime.datetime.now().astimezone()


def _path(notes_dir=None):
    return os.path.join(records.store_dir(notes_dir), SESSION_FILE)


def new_state(previous=None):
    s = {"id": uuid.uuid4().hex[:10], "started": _now().isoformat(timespec="seconds"),
         "updated": _now().isoformat(timespec="seconds"), "turns": 0,
         "topic": [], "objective": None, "entities": [], "notes": [], "decisions": [], "projects": [],
         "tasks": [], "open_question": None, "pending_offer": None, "last_user": None,
         "current_model": None, "previous_models": [], "switch_reason": None,
         "previous_topic": (previous or {}).get("topic") or None}
    return s


def load(notes_dir=None):
    try:
        with open(_path(notes_dir), encoding="utf-8") as f:
            s = json.load(f)
        return s if isinstance(s, dict) and s.get("id") else new_state()
    except (OSError, ValueError):
        return new_state()


def save(state, notes_dir=None):
    try:
        records._atomic_write_json(_path(notes_dir), state)
    except OSError:
        pass


def current(notes_dir=None, now=None):
    """This conversation's state; a new one after a long quiet spell."""
    with _LOCK:
        s = load(notes_dir)
        try:
            quiet = ((now or _now()) - datetime.datetime.fromisoformat(s["updated"])).total_seconds() / 60
        except (KeyError, ValueError, TypeError):
            quiet = 0
        if s.get("turns") and quiet > NEW_SESSION_AFTER_MIN:
            s = new_state(s)
            save(s, notes_dir)
        return s


def reset(notes_dir=None):
    with _LOCK:
        s = new_state(load(notes_dir))
        save(s, notes_dir)
        return s


def _keywords(text, n=6):
    words = [w for w in re.findall(r"[a-z][a-z0-9'-]+", (text or "").lower()) if w not in STOP and len(w) > 2]
    seen, out = set(), []
    for w in words:
        if w not in seen:
            seen.add(w); out.append(w)
    return out[:n]


def _merge(old, new, keep):
    out = list(new) + [x for x in old if x not in new]
    return out[:keep]


def before_turn(state, question, notes):
    """Updates the state with the question and the notes retrieval found.
    notes: the graph nodes from retrieval (with type/state/title)."""
    q = question.strip()
    state["last_user"] = q[:500]
    kw = _keywords(q)
    if kw:
        state["topic"] = _merge(state.get("topic", []), kw, 8)
        if len(kw) >= 2 and not re.match(r"^(thanks|thank you|cheers|ok|okay|great|nice)\b", q.lower()):
            state["objective"] = q[:300]
    ents = re.findall(r"\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)*)\b", q)
    ents = [e for e in ents if e.lower() not in STOP and e not in ("I",)]
    state["entities"] = _merge(state.get("entities", []), ents, 10)
    by_type = {"decision": "decisions", "project": "projects", "task": "tasks", "commitment": "tasks", "waiting": "tasks"}
    for n in notes:
        item = {"id": n.get("id"), "title": n.get("label"), "type": n.get("type"),
                "current": n.get("current", True)}
        state["notes"] = _merge([x for x in state.get("notes", []) if x.get("title") != item["title"]], [item], KEEP_NOTES)
        k = by_type.get(n.get("type"))
        if k:
            state[k] = _merge([x for x in state.get(k, []) if x != n.get("label")], [n.get("label")], 5)
    return state


OFFER_RE = re.compile(r"(shall i|would you like me to|want me to|should i|do you want me to)[^?]*\?", re.I)


def after_turn(state, answer, model_used, switch_note=None, notes_dir=None):
    """Records Jarvis's side of the turn and which model spoke."""
    state["turns"] = state.get("turns", 0) + 1
    state["updated"] = _now().isoformat(timespec="seconds")
    a = (answer or "").strip()
    m = OFFER_RE.search(a)
    state["pending_offer"] = m.group(0)[:200] if m else None
    qs = [s for s in re.split(r"(?<=[.!?])\s+", a) if s.endswith("?")]
    state["open_question"] = qs[-1][:200] if qs else None
    if model_used and model_used != state.get("current_model"):
        if state.get("current_model"):
            state["previous_models"] = _merge(state.get("previous_models", []), [state["current_model"]], 5)
            state["switch_reason"] = switch_note or "the previous model didn't answer this time"
        state["current_model"] = model_used
    save(state, notes_dir)
    return state


def state_summary(state):
    """The session state as a few plain lines for the model."""
    lines = []
    if state.get("turns"):
        lines.append(f"This conversation so far: {state['turns']} exchange(s).")
    else:
        lines.append("This is the start of a new conversation."
                     + (f" Last time was about: {', '.join(state['previous_topic'])}." if state.get("previous_topic") else ""))
    if state.get("topic"):
        lines.append("Topic: " + ", ".join(state["topic"]) + ".")
    if state.get("objective"):
        lines.append(f"What the user is after: \"{state['objective']}\"")
    if state.get("entities"):
        lines.append("People, places and things mentioned: " + ", ".join(state["entities"]) + ".")
    if state.get("notes"):
        lines.append("Notes already discussed: " + "; ".join(
            n["title"] + ("" if n.get("current", True) else " (no longer current)") for n in state["notes"] if n.get("title")) + ".")
    for k, word in (("decisions", "Decisions in play"), ("projects", "Projects in play"), ("tasks", "Tasks in play")):
        if state.get(k):
            lines.append(f"{word}: " + "; ".join(state[k]) + ".")
    if state.get("pending_offer"):
        lines.append(f"Jarvis last offered: \"{state['pending_offer']}\" -- the user's reply may be answering it.")
    elif state.get("open_question"):
        lines.append(f"Jarvis last asked: \"{state['open_question']}\" -- the user's reply may be answering it.")
    return "\n".join(lines)


# ---- privacy: hide what a model never needs (brief 3 §24) --------------------------------

SECRET_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_-]{16,}"), "[a key, hidden]"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), None),                      # card numbers: checked below
    (re.compile(r"(?im)^(.*\b(?:password|passcode|pin|passwd)\b\s*[:=]\s*)(\S.*)$"), r"\1[hidden]"),
    (re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}\b"), "[an IBAN, hidden]"),
]


def _luhn(digits):
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d; alt = not alt
    return total % 10 == 0


def redact(text):
    """(text, how many things were hidden)."""
    hidden = 0
    for rx, rep in SECRET_PATTERNS:
        if rep is None:
            def card(m):
                nonlocal hidden
                d = re.sub(r"\D", "", m.group(0))
                if 13 <= len(d) <= 19 and _luhn(d):
                    hidden += 1
                    return "[a card number, hidden]"
                return m.group(0)
            text = rx.sub(card, text)
        else:
            text, n = rx.subn(rep, text)
            hidden += n
    return text, hidden


# ---- the task packet ---------------------------------------------------------------------------

TASKS = {
    "conversation": ("Answer the user's message in conversation, from the relevant notes when the question "
                     "is about them.",
                     "Two or three sentences, in character. Plain text, no lists unless asked."),
}


def build_packet(request, state, notes_block, history=(), task_type="conversation", personality=None,
                 max_history_turns=6):
    """The messages for the model. Returns (messages, hidden_count)."""
    what, output = TASKS.get(task_type, TASKS["conversation"])
    memory, hidden = redact(notes_block or "")
    state_text, n_state = redact(state_summary(state))     # the state can quote note titles too
    hidden += n_state
    sections = [
        CORE_RULES + (personality or PERSONALITY),
        f"TASK TYPE: {task_type}. {what}",
        "CURRENT SESSION STATE (kept by Jarvis, not by any model):\n" + state_text,
        "RELEVANT MEMORY (the user's notes that matter for this message; the only notes you may draw on):\n"
        + (memory if memory.strip() else "(no notes match this message)"),
        "AVAILABLE TOOLS: none. You cannot change, save, send or schedule anything. If the user wants a change, "
        "tell them what to say (e.g. \"remember that...\", \"link X and Y\") and Jarvis will do it.",
        "CONSTRAINTS: answer only from the notes above and this conversation; notes marked NOT current are the "
        "past; unconfirmed readings are Jarvis's guess, not the user's words; never invent notes, people, dates "
        "or actions." + (" Some private details (passwords, keys, card numbers) were hidden before you saw "
                          "the notes; say so if it matters." if hidden else ""),
        f"REQUIRED OUTPUT: {output}",
    ]
    msgs = [{"role": "system", "content": "\n\n".join(sections)}]
    for h in list(history)[-max_history_turns * 2:]:
        content, _ = redact(h.get("content", ""))
        msgs.append({"role": h.get("role", "user"), "content": content})
    req, n = redact(request)
    msgs.append({"role": "user", "content": req})
    return msgs, hidden + n
