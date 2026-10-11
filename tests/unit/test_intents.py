from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from freddy.fastpath.intents import (
    AddGoal,
    AddReminderOnce,
    AddReminderRecurring,
    CancelReminder,
    CompleteGoal,
    Intent,
    ListGoals,
    ListReminders,
    parse,
)

TZ = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 11, 16, 0, tzinfo=TZ)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("goals", ListGoals()),
        ("What are my goals?", ListGoals()),
        ("show my goals", ListGoals()),
        ("today", ListGoals()),
        ("add goal: Read 20 pages", AddGoal("Read 20 pages")),
        ("Add a goal hit the gym", AddGoal("hit the gym")),
        ("goal: call the bank.", AddGoal("call the bank")),
        ("done 12", CompleteGoal(goal_id=12)),
        ("done #3", CompleteGoal(goal_id=3)),
        ("finished gym", CompleteGoal(query="gym")),
        ("done with reading", CompleteGoal(query="reading")),
        ("mark 4 as done", CompleteGoal(goal_id=4)),
        ("reminders", ListReminders()),
        ("cancel reminder 5", CancelReminder(5)),
        ("delete reminder #7", CancelReminder(7)),
        (
            "remind me in 20 min to stretch",
            AddReminderOnce("stretch", datetime(2026, 10, 11, 16, 20, tzinfo=TZ)),
        ),
        (
            "remind me at 6pm to call Mom",
            AddReminderOnce("call Mom", datetime(2026, 10, 11, 18, 0, tzinfo=TZ)),
        ),
        (
            "remind me to go to the gym at 7pm",
            AddReminderOnce("go to the gym", datetime(2026, 10, 11, 19, 0, tzinfo=TZ)),
        ),
        (
            "remind me to check in on mom tomorrow at 9am",
            AddReminderOnce("check in on mom", datetime(2026, 10, 12, 9, 0, tzinfo=TZ)),
        ),
        (
            "remind me every weekday at 7:30 to take vitamins",
            AddReminderRecurring("take vitamins", "30 7 * * 1-5"),
        ),
        (
            "remind me to drink water every day at 15:00",
            AddReminderRecurring("drink water", "0 15 * * *"),
        ),
    ],
)
def test_matches(text: str, expected: Intent) -> None:
    assert parse(text, NOW, TZ) == expected


@pytest.mark.parametrize(
    "text",
    [
        "hey freddy",
        "add goal: gym tomorrow",
        "add goal: read and gym",
        "done gym and reading",
        "remind me tomorrow at 9 to pay rent",
        "remind me at 9am to wake up",
        "remind me to do stuff",
        "remind me about the thing later",
        "what should I focus on today?",
        "goals for next week",
        "done",
        "add goal: x\nand also y",
    ],
)
def test_falls_through(text: str) -> None:
    assert parse(text, NOW, TZ) is None
