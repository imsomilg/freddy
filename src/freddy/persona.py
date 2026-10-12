import random

HONORIFIC = "sir"

LINES: dict[str, tuple[str, ...]] = {
    "goal_added": (
        "Very good, sir.",
        "Noted, sir. It's on the list.",
        "Consider it scheduled for greatness, sir.",
        "An excellent ambition, sir.",
        "I've written it down in my best handwriting, sir.",
    ),
    "goal_done": (
        "Splendid work, sir.",
        "Another one falls, sir.",
        "Most impressive, sir.",
        "I'll polish the trophy, sir.",
        "Exactly as I expected of you, sir.",
    ),
    "all_done": (
        "That's everything for today, sir 🎉",
        "The list is conquered, sir 🎉",
        "Not a single goal left standing, sir 🎉",
    ),
    "reminder_set": (
        "I shall see to it, sir.",
        "Consider it done, sir.",
        "I won't let you forget, sir.",
        "It's in the diary, sir.",
    ),
    "cancelled": (
        "Struck from the record, sir.",
        "As if it never existed, sir.",
        "Quietly disposed of, sir.",
    ),
    "no_goals": (
        "A clean slate today, sir. What shall we conquer?",
        "Nothing on the list yet, sir. Your orders?",
        "The day is unclaimed, sir. What are we going for?",
    ),
    "no_reminders": (
        "No reminders set, sir. Your memory is trusted entirely.",
        "Nothing in the diary, sir.",
    ),
    "reminder": (
        "Pardon the interruption, sir:",
        "A gentle reminder, sir:",
        "You asked me to mention, sir:",
        "Ahem, sir:",
    ),
    "morning": (
        "Good morning, sir.",
        "Rise and shine, sir.",
        "Good morning, sir. The day awaits your command.",
    ),
    "evening": (
        "Good evening, sir.",
        "Evening, sir. Time for the report.",
        "Good evening, sir. Shall we review the day?",
    ),
    "boot": (
        "I am at your service, sir.",
        "At your service, sir.",
        "Reporting for duty, sir.",
        "Back on duty, sir. Did you miss me?",
    ),
    "week_sweep": (
        "Clean sweep 🎉 I've taken the liberty of being impressed, sir.",
        "Clean sweep 🎉 Frankly, sir, the bar is now unreasonably high.",
    ),
    "week_strong": (
        "A fine week, sir. What are we going for next week?",
        "Rather good form, sir. Same again next week?",
    ),
    "week_quiet": (
        "A quieter week, sir. What are we going for next week?",
        "Every great comeback needs a quiet week first, sir. Plans for next week?",
    ),
}

_rng = random.Random()
_last: dict[str, str] = {}


def line(kind: str) -> str:
    pool = LINES[kind]
    choices = [x for x in pool if x != _last.get(kind)] or list(pool)
    pick = _rng.choice(choices)
    _last[kind] = pick
    return pick
