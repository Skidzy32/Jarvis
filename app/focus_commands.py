"""
focus_commands.py — natural-language parsing for focus-session voice/chat
commands. Kept separate from focus_session.py (pure state/logic) and from
server.py (HTTP plumbing), so parsing is testable on its own — see
test_focus_commands.py.

Extended for the full Prompt 09 spec: start/stop/status plus snooze,
excuse, pause, resume, extend, abort, and setting the nag cadence
("call me out every thirty seconds").
"""

import re

LEADING_FILLERS = {"jarvis", "please", "hey", "ok", "okay"}

DEFAULT_MINUTES = 25  # classic pomodoro fallback when no duration is given

START_TRIGGER_RE = re.compile(
    r"\b(start|begin|kick off|kick-off)\b.{0,15}\bfocus\b.{0,15}\bsession\b"
    r"|\bfocus session on\b"
    r"|\bhelp me focus on\b",
    re.IGNORECASE,
)

STOP_TRIGGER_RE = re.compile(
    r"\b(stop|end|finish)\b.{0,15}\bfocus\b(.{0,15}\bsession\b)?",
    re.IGNORECASE,
)

ABORT_TRIGGER_RE = re.compile(
    r"\babort\b.{0,15}\bfocus\b(.{0,15}\bsession\b)?"
    r"|\babort\b.{0,15}\bsession\b"
    r"|\bcancel\b.{0,15}\bfocus\b(.{0,15}\bsession\b)?"
    r"|\bcancel\b.{0,15}\bsession\b",
    re.IGNORECASE,
)

STATUS_TRIGGER_RE = re.compile(
    r"\bfocus (session )?status\b"
    r"|\bhow (much|long).{0,15}(is )?left\b"
    r"|\bhow am i doing on my focus\b",
    re.IGNORECASE,
)

PAUSE_TRIGGER_RE = re.compile(r"\bpause\b.{0,15}\b(focus|session)\b|\bpause my session\b", re.IGNORECASE)
RESUME_TRIGGER_RE = re.compile(r"\bresume\b.{0,15}\b(focus|session)\b|\bresume my session\b|\bunpause\b", re.IGNORECASE)

EXTEND_TRIGGER_RE = re.compile(
    r"\b(extend|add more time to|give me more time on)\b.{0,15}\b(focus|session)\b"
    r"|\bextend\b.{0,10}\bby\b",
    re.IGNORECASE,
)

SNOOZE_TRIGGER_RE = re.compile(
    r"\bsnooze\b|\bgive me (a|an|\d+)\s*(minute|min|second|sec)",
    re.IGNORECASE,
)

EXCUSE_TRIGGER_RE = re.compile(
    r"\bit'?s (okay|ok)[,.]?\s*i'?m\b"
    r"|\bi'?m (doing|just doing)\b.{0,20}\b(research|reference|looking something up)\b"
    r"|\bthis is (part of|related to) (my|the) (work|task)\b",
    re.IGNORECASE,
)

# Prompt 11: "lock on this tab", "keep me in this tab", "this is the tab",
# "stay on this tab", and the natural lead-in version ("okay, I'm gonna need
# you to keep me in this tab"). "app"/"window" are accepted alongside "tab"
# because the same command locks a non-browser app, and "keep me in this
# app" is what you'd naturally say there. Checked BEFORE every other focus
# command and before any screen-share routing, so mid-session "lock this
# tab" always means the focus target.
RETARGET_TRIGGER_RE = re.compile(
    r"\block\s+(?:on\s*(?:to)?\s+|onto\s+|in\s+)?(?:to\s+)?this\s+(?:tab|app|window)\b"
    r"|\bkeep\s+me\s+(?:in|on)\s+this\s+(?:tab|app|window)\b"
    r"|\bthis\s+is\s+the\s+(?:tab|app|window)\b"
    r"|\bstay\s+(?:on|in)\s+this\s+(?:tab|app|window)\b",
    re.IGNORECASE,
)

# Prompt 12: the drill-sergeant callouts, "for the days I ask for that".
# OFF is checked first, so "drill sergeant off" never reads as ON.
DRILL_OFF_TRIGGER_RE = re.compile(
    r"\bdrill[\s-]*sergeant(?:\s+mode)?\s+off\b"
    r"|\b(?:stop|no more|enough(?: of)?|turn off|disable)\s+(?:the\s+)?drill[\s-]*sergeant\b"
    r"|\bgo easy on me\b",
    re.IGNORECASE,
)
DRILL_ON_TRIGGER_RE = re.compile(
    r"\bdrill[\s-]*sergeant\s+(?:mode|on)\b"
    r"|\b(?:be|act like|act as)\s+(?:a|my)\s+drill[\s-]*sergeant\b"
    r"|\b(?:turn on|enable|switch to|go)\s+(?:the\s+)?drill[\s-]*sergeant\b",
    re.IGNORECASE,
)

NAG_INTERVAL_TRIGGER_RE = re.compile(
    r"\bcall me out every\b|\bnag me every\b|\bcheck (on|in with) me every\b",
    re.IGNORECASE,
)

MINUTES_RE = re.compile(
    r"for\s+(\d+)\s*(minutes?|mins?|m\b)"
    r"|(\d+)\s*(minutes?|mins?)\s*(session|focus|of)",
    re.IGNORECASE,
)

# Extracts a bare number of minutes for "extend by 10 minutes" / "extend 10 minutes"
EXTEND_MINUTES_RE = re.compile(r"(\d+)\s*(minutes?|mins?)", re.IGNORECASE)

# Extracts seconds/minutes for snooze ("give me fifteen seconds", "snooze for 2 minutes")
SNOOZE_AMOUNT_RE = re.compile(r"(\d+)\s*(minutes?|mins?|seconds?|secs?)", re.IGNORECASE)

# Extracts seconds/minutes for nag interval ("call me out every thirty seconds")
NAG_AMOUNT_RE = re.compile(r"every\s+(\d+)\s*(minutes?|mins?|seconds?|secs?)", re.IGNORECASE)

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "fifteen": 15,
    "twenty": 20, "thirty": 30, "forty": 40, "fortyfive": 45,
    "fifty": 50, "sixty": 60,
}


def _words_to_digits(text):
    """Speech recognition sometimes gives us 'thirty seconds' as words,
    not digits — normalize the common ones before the regexes run."""
    def replace(match):
        word = match.group(0).lower()
        return str(NUMBER_WORDS.get(word, word))
    pattern = r"\b(" + "|".join(NUMBER_WORDS.keys()) + r")\b"
    return re.sub(pattern, replace, text, flags=re.IGNORECASE)


# Trims trigger phrasing and duration phrasing off the raw text to leave
# just the task description.
TASK_EXTRACT_RE = re.compile(
    r"^(jarvis|please|hey|ok|okay|\s)*"
    r"(?:"
    r"(?:start|begin|kick off|kick-off)\s+(?:a |my )?focus session(?:\s+on)?"
    r"|focus session(?:\s+on)?"
    r"|help me focus on"
    r")\s*",
    re.IGNORECASE,
)


def _strip_leading_fillers(text):
    words = text.strip().split()
    while words and words[0].strip(",.!?").lower() in LEADING_FILLERS:
        words.pop(0)
    return " ".join(words)


def is_start_command(text):
    return bool(START_TRIGGER_RE.search(text))


def is_stop_command(text):
    return bool(STOP_TRIGGER_RE.search(text)) and not is_abort_command(text)


def is_abort_command(text):
    return bool(ABORT_TRIGGER_RE.search(text))


def is_status_command(text):
    return bool(STATUS_TRIGGER_RE.search(text))


def is_pause_command(text):
    return bool(PAUSE_TRIGGER_RE.search(text))


def is_resume_command(text):
    return bool(RESUME_TRIGGER_RE.search(text))


def is_extend_command(text):
    return bool(EXTEND_TRIGGER_RE.search(text))


def is_snooze_command(text):
    # Speech gives us "fifteen seconds" as words as often as "15 seconds"
    # as digits — normalize before matching so both phrasings trigger.
    return bool(SNOOZE_TRIGGER_RE.search(_words_to_digits(text)))


def is_excuse_command(text):
    return bool(EXCUSE_TRIGGER_RE.search(text))


def is_nag_interval_command(text):
    return bool(NAG_INTERVAL_TRIGGER_RE.search(text))


def is_retarget_command(text):
    return bool(RETARGET_TRIGGER_RE.search(text))


def is_drill_off_command(text):
    return bool(DRILL_OFF_TRIGGER_RE.search(text))


def is_drill_on_command(text):
    return bool(DRILL_ON_TRIGGER_RE.search(text)) and not is_drill_off_command(text)


def parse_start_command(text):
    """Returns (task, minutes). `task` is None for a genuinely freeform
    session — FocusSession with target_hash still locks onto whatever
    you're doing when it starts; only the spoken task label is optional."""
    cleaned = _strip_leading_fillers(text)

    minutes = DEFAULT_MINUTES
    m = MINUTES_RE.search(cleaned)
    if m:
        digits = m.group(1) or m.group(3)
        if digits:
            minutes = max(1, int(digits))

    without_minutes = MINUTES_RE.sub("", cleaned).strip(" ,.!?")
    task_text = TASK_EXTRACT_RE.sub("", without_minutes).strip(" ,.!?")
    task_text = re.sub(r"^on\s+", "", task_text, flags=re.IGNORECASE).strip()

    return (task_text if task_text else None), minutes


def _amount_to_seconds(match_text, amount_re):
    normalized = _words_to_digits(match_text)
    m = amount_re.search(normalized)
    if not m:
        return None
    value = int(m.group(1))
    unit = m.group(2).lower()
    return value * 60 if unit.startswith("min") else value


def parse_snooze_seconds(text, default_seconds):
    seconds = _amount_to_seconds(text, SNOOZE_AMOUNT_RE)
    return seconds if seconds is not None else default_seconds


def parse_extend_minutes(text, default_minutes=10):
    normalized = _words_to_digits(text)
    m = EXTEND_MINUTES_RE.search(normalized)
    return int(m.group(1)) if m else default_minutes


def parse_nag_interval_seconds(text, default_seconds):
    seconds = _amount_to_seconds(text, NAG_AMOUNT_RE)
    return seconds if seconds is not None else default_seconds


if __name__ == "__main__":
    samples = [
        "Jarvis, start a focus session on the Acme proposal for 25 minutes",
        "stop my focus session",
        "abort the focus session",
        "pause my focus session",
        "resume my session",
        "extend my session by 10 minutes",
        "give me fifteen seconds",
        "snooze for 2 minutes",
        "it's okay, I'm doing research",
        "call me out every thirty seconds",
        "focus session status",
    ]
    for s in samples:
        tags = []
        if is_start_command(s): tags.append("START:" + str(parse_start_command(s)))
        if is_stop_command(s): tags.append("STOP")
        if is_abort_command(s): tags.append("ABORT")
        if is_pause_command(s): tags.append("PAUSE")
        if is_resume_command(s): tags.append("RESUME")
        if is_extend_command(s): tags.append(f"EXTEND:{parse_extend_minutes(s)}")
        if is_snooze_command(s): tags.append(f"SNOOZE:{parse_snooze_seconds(s, 15)}")
        if is_excuse_command(s): tags.append("EXCUSE")
        if is_nag_interval_command(s): tags.append(f"NAG_INTERVAL:{parse_nag_interval_seconds(s, 30)}")
        if is_status_command(s): tags.append("STATUS")
        print(f"{s!r:60} -> {tags}")
