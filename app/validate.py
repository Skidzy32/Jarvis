"""
validate.py — 4.8.0 (addendum §9-10): an answer that arrived isn't
necessarily a good answer. Deterministic checks first, no second AI:

  hard  (never shown; try another model)
    empty            nothing came back
    safety_verdict   a guard model's verdict ("the user is safe") instead of an answer
  soft  (try one other model; if that's no better, the best answer is used)
    claimed_action   "I've saved / added / linked / sent / scheduled..." --
                     the model can only talk; Jarvis's own code acts
    invented_note    it names a note of yours that doesn't exist
    stale_as_current it presents a note Jarvis marked NOT current (replaced,
                     reversed, historical) as how things are now
    ignored_request  a long answer that shares nothing with the question
                     or the notes (e.g. a canned reply)

Standard library only.
"""

import re

HARD = ("empty", "safety_verdict", "malformed")
CLAIM_RE = re.compile(
    r"\b(i(?:'ve| have)|i've just|i just|done[,.!]? i(?:'ve)?)\s+(?:gone ahead and\s+)?"
    r"(saved|added|created|linked|filed|deleted|removed|sent|emailed|scheduled|booked|moved|updated|"
    r"set (?:a|up a|you a) reminder|put (?:it|that|them) in|noted (?:it|that) (?:down|in your notes))\b", re.I)
NOTE_NAME_RE = re.compile(r"\bnote\s+(?:called|titled|named|about)\s+[\"'‘“]([^\"'’”]{3,80})[\"'’”]", re.I)
PAST_RE = re.compile(r"\b(used to|previously|before|earlier|no longer|once|was|were|had|formerly|in the past|"
                     r"originally|at one point|superseded|replaced|reversed|changed)\b", re.I)
WORD_RE = re.compile(r"[a-z]{3,}")
COMMON = {"that", "this", "with", "have", "your", "from", "they", "their", "there", "about", "would", "could",
          "should", "which", "what", "when", "where", "sir", "notes", "note", "just", "like", "will", "been",
          "into", "than", "then", "them", "also", "some", "more", "very", "much", "well", "here", "the", "and",
          "you", "for", "are", "but", "not", "was", "has", "had", "its", "now", "yes", "can", "our", "all", "any"}


def _words(t):
    return {w for w in WORD_RE.findall((t or "").lower()) if w not in COMMON}


def check_chat(answer, question="", notes=(), all_titles=(), unusable=None):
    """-> (ok, kind, reason). notes: the notes given to the model, as dicts
    with label/excerpt/current. all_titles: every note title (for invented
    notes). unusable: the existing safety-verdict test, if any."""
    a = (answer or "").strip()
    if not a:
        return False, "empty", "no answer"
    if unusable and unusable(a):
        return False, "safety_verdict", "a safety verdict, not an answer"
    m = CLAIM_RE.search(a)
    if m and not re.search(r"\b(if you|say|tell me|want me to|shall i|should i|could)\b", a[max(0, m.start() - 40):m.start()], re.I):
        return False, "claimed_action", f"claimed to have {m.group(2).lower()} something"
    titles = {t.lower().strip() for t in all_titles}
    for name in NOTE_NAME_RE.findall(a):
        n = name.lower().strip()
        if titles and not any(n in t or t in n for t in titles):
            return False, "invented_note", f"named a note that doesn't exist: '{name}'"
    old = [n for n in notes if n.get("current") is False]
    cur = [n for n in notes if n.get("current") is not False]
    if old and not PAST_RE.search(a):
        aw = _words(a)
        for n in old:
            distinct = _words(f"{n.get('label', '')} {n.get('excerpt', '')}") - set().union(*(_words(
                f"{c.get('label', '')} {c.get('excerpt', '')}") for c in cur)) if cur else _words(
                f"{n.get('label', '')} {n.get('excerpt', '')}")
            if distinct and len(aw & distinct) >= (1 if len(distinct) <= 3 else 2):
                return False, "stale_as_current", f"presented '{n.get('label')}' (no longer current) as current"
    if len(a) > 200 and (notes or question):
        context = _words(question) | set().union(*(_words(f"{n.get('label', '')} {n.get('excerpt', '')}") for n in notes)) \
            if notes else _words(question)
        if context and not (_words(a) & context):
            return False, "ignored_request", "didn't address the question or the notes"
    return True, None, None


def check_json(answer):
    """For plans and sorting (TOOL): the reply must contain a JSON object."""
    import json
    a = answer or ""
    for m in re.finditer(r"\{", a):
        depth = 0
        for i in range(m.start(), len(a)):
            depth += {"{": 1, "}": -1}.get(a[i], 0)
            if depth == 0:
                try:
                    json.loads(a[m.start():i + 1])
                    return True, None, None
                except ValueError:
                    break
    return False, "malformed", "no usable JSON in the reply"


CORRECTION_RE = re.compile(r"^(no[,.!]|nope|that'?s (wrong|not right|incorrect|not true)|wrong[,.!]|incorrect|"
                           r"you'?re wrong|not quite|i (told|said) you|that isn'?t (right|true))", re.I)


def is_correction(message):
    """The user pushing back straight after an answer -- a WEAK signal the
    last answer missed (they may just have changed their mind)."""
    return bool(CORRECTION_RE.search((message or "").strip()))
