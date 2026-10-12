from datetime import datetime, time
from zoneinfo import ZoneInfo

import discord

from freddy.assistant.commands import SlashCommands, checkin_kind
from freddy.assistant.service import UserLocks
from freddy.channels.discord_adapter import DiscordChannel
from freddy.db.models import ReminderKind, User

TZ = ZoneInfo("Asia/Kolkata")


def owner() -> User:
    return User(
        discord_id=1, timezone="Asia/Kolkata", morning_checkin=time(8), evening_checkin=time(21)
    )


def at(h: int, m: int = 0) -> datetime:
    return datetime(2026, 10, 12, h, m, tzinfo=TZ)


def test_checkin_kind_by_time_of_day() -> None:
    assert checkin_kind(owner(), at(7), None) == ReminderKind.CHECKIN_MORNING
    assert checkin_kind(owner(), at(14, 29), None) == ReminderKind.CHECKIN_MORNING
    assert checkin_kind(owner(), at(14, 30), None) == ReminderKind.CHECKIN_EVENING
    assert checkin_kind(owner(), at(23), None) == ReminderKind.CHECKIN_EVENING


def test_checkin_kind_explicit() -> None:
    assert checkin_kind(owner(), at(7), "evening") == ReminderKind.CHECKIN_EVENING
    assert checkin_kind(owner(), at(23), "morning") == ReminderKind.CHECKIN_MORNING


def test_commands_registered_for_guild_only() -> None:
    commands = SlashCommands(None, UserLocks())  # type: ignore[arg-type]
    channel = DiscordChannel("t", 10, 20, 30, handler=None, commands=commands)  # type: ignore[arg-type]
    names = {c.name for c in channel.tree.get_commands(guild=discord.Object(10))}
    assert names == {"goals", "done", "remind", "reminders", "checkin"}
    assert channel.tree.get_commands() == []
