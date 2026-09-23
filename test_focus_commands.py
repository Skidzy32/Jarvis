"""Real-execution test for focus_commands.py parsing, including the
Prompt 09 voice controls (snooze, excuse, pause, resume, extend, abort,
nag interval). Run directly."""

import focus_commands as fc

PASS = 0
FAIL = 0


def check(label, condition):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK]   {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}")


print("Start command parsing (unchanged behavior)")
cases = [
    ("Jarvis, start a focus session on the Acme proposal for 25 minutes", "the Acme proposal", 25),
    ("start focus session for 10 minutes", None, 10),
    ("help me focus on writing the report for 45 min", "writing the report", 45),
]
for text, expected_task, expected_minutes in cases:
    check(f"{text!r} is a start command", fc.is_start_command(text))
    task, minutes = fc.parse_start_command(text)
    check(f"  task == {expected_task!r}", task == expected_task)
    check(f"  minutes == {expected_minutes}", minutes == expected_minutes)

print("\nStop vs abort are distinct and mutually exclusive")
check("'stop my focus session' is stop", fc.is_stop_command("stop my focus session"))
check("'stop my focus session' is NOT abort", not fc.is_abort_command("stop my focus session"))
check("'abort the focus session' is abort", fc.is_abort_command("abort the focus session"))
check("'abort the focus session' is NOT stop", not fc.is_stop_command("abort the focus session"))
check("'cancel my session' is abort", fc.is_abort_command("cancel my session"))

print("\nPause / resume")
check("'pause my focus session' is pause", fc.is_pause_command("pause my focus session"))
check("'resume my session' is resume", fc.is_resume_command("resume my session"))
check("'unpause' is resume", fc.is_resume_command("unpause"))
check("'pause my focus session' is NOT resume", not fc.is_resume_command("pause my focus session"))

print("\nExtend, with minutes parsed")
check("'extend my session by 10 minutes' is extend", fc.is_extend_command("extend my session by 10 minutes"))
check("  parses 10 minutes", fc.parse_extend_minutes("extend my session by 10 minutes") == 10)
check("'extend by 5 minutes' parses 5", fc.parse_extend_minutes("extend session by 5 minutes") == 5)
check("extend with no number falls back to default", fc.parse_extend_minutes("extend my focus session") == 10)

print("\nSnooze, digits and spoken number-words both work")
check("'give me fifteen seconds' is snooze", fc.is_snooze_command("give me fifteen seconds"))
check("  parses to 15 seconds", fc.parse_snooze_seconds("give me fifteen seconds", 999) == 15)
check("'snooze for 2 minutes' is snooze", fc.is_snooze_command("snooze for 2 minutes"))
check("  parses to 120 seconds", fc.parse_snooze_seconds("snooze for 2 minutes", 999) == 120)
check("bare 'snooze' with no amount is still detected", fc.is_snooze_command("snooze"))
check("  falls back to the given default", fc.parse_snooze_seconds("snooze", 15) == 15)

print("\nExcuse phrasing")
for text in ["it's okay, I'm doing research", "its ok im doing research", "I'm just doing research for this"]:
    check(f"{text!r} recognized as excuse", fc.is_excuse_command(text))

print("\nNag interval, spoken numbers")
check("'call me out every thirty seconds' recognized", fc.is_nag_interval_command("call me out every thirty seconds"))
check("  parses to 30 seconds", fc.parse_nag_interval_seconds("call me out every thirty seconds", 999) == 30)
check("'nag me every 2 minutes' recognized", fc.is_nag_interval_command("nag me every 2 minutes"))
check("  parses to 120 seconds", fc.parse_nag_interval_seconds("nag me every 2 minutes", 999) == 120)

print("\nStatus phrasing (unchanged)")
for text in ["focus session status", "how much time is left", "how long is left"]:
    check(f"{text!r} recognized as status", fc.is_status_command(text))

print("\nFalse-positive guard: ordinary chat must not trigger any focus command")
non_commands = [
    "what's the status of the Acme rebrand project",
    "how long has Jamie worked here",
    "can you help me focus my search results",
    "tell me about Porter Fitness",
    "give me a second to think",
    "cancel my subscription reminder",
]
for text in non_commands:
    check(f"{text!r} triggers no start/stop/abort/pause/resume/extend", not any([
        fc.is_start_command(text), fc.is_stop_command(text), fc.is_abort_command(text),
        fc.is_pause_command(text), fc.is_resume_command(text), fc.is_extend_command(text),
    ]))

print("\nPrompt 11: re-target phrases (the spec's exact list plus the lead-in version)")
retarget_phrases = [
    "lock on this tab",
    "keep me in this tab",
    "this is the tab",
    "stay on this tab",
    "okay, I'm gonna need you to keep me in this tab.",
    "Jarvis, lock onto this tab",
    "lock this tab",
    "keep me in this app",
    "Lock on to this window please",
]
for text in retarget_phrases:
    check(f"{text!r} is a re-target", fc.is_retarget_command(text))
    check(f"{text!r} is NOT also read as stop/abort/pause/start", not any([
        fc.is_start_command(text), fc.is_stop_command(text), fc.is_abort_command(text),
        fc.is_pause_command(text),
    ]))

print("\nPrompt 11: ordinary chat must not re-target")
not_retargets = [
    "what's in this tab's notes",
    "how do I lock my screen",
    "tell me about the tab system we use",
    "is this the right approach",
    "keep me posted on the Acme project",
    "stay on top of the invoices",
]
for text in not_retargets:
    check(f"{text!r} is not a re-target", not fc.is_retarget_command(text))

print("\nPrompt 12: drill-sergeant mode on/off")
for text in ["drill sergeant mode", "Jarvis, drill sergeant mode please", "be my drill sergeant today",
             "turn on the drill sergeant", "drill sergeant on", "drill-sergeant mode"]:
    check(f"{text!r} turns it ON (and not off)", fc.is_drill_on_command(text) and not fc.is_drill_off_command(text))
for text in ["drill sergeant off", "drill sergeant mode off", "go easy on me",
             "stop the drill sergeant", "enough drill sergeant"]:
    check(f"{text!r} turns it OFF (and not on)", fc.is_drill_off_command(text) and not fc.is_drill_on_command(text))
for text in ["what is a drill sergeant", "my dad was a drill sergeant"]:
    check(f"{text!r} is ordinary chat, not a toggle",
          not fc.is_drill_on_command(text) and not fc.is_drill_off_command(text))

print(f"\n{PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)
