import enum
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class GoalStatus(enum.StrEnum):
    OPEN = "open"
    DONE = "done"
    DROPPED = "dropped"


class ReminderKind(enum.StrEnum):
    REMINDER = "reminder"
    CHECKIN_MORNING = "checkin_morning"
    CHECKIN_EVENING = "checkin_evening"
    WEEKLY_SUMMARY = "weekly_summary"


class MessageSource(enum.StrEnum):
    USER = "user"
    FASTPATH = "fastpath"
    CLAUDE = "claude"
    SCHEDULER = "scheduler"
    BRIDGE = "bridge"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    discord_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    timezone: Mapped[str] = mapped_column(String(64))
    morning_checkin: Mapped[time] = mapped_column(Time)
    evening_checkin: Mapped[time] = mapped_column(Time)
    weekly_summary_delay: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Goal(Base):
    __tablename__ = "goals"
    __table_args__ = (Index("ix_goals_user_date", "user_id", "for_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(500))
    for_date: Mapped[date] = mapped_column(Date)
    status: Mapped[GoalStatus] = mapped_column(
        Enum(GoalStatus, name="goal_status", values_callable=lambda e: [m.value for m in e]),
        default=GoalStatus.OPEN,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Reminder(Base):
    __tablename__ = "reminders"
    __table_args__ = (Index("ix_reminders_due", "active", "next_run_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[ReminderKind] = mapped_column(
        Enum(ReminderKind, name="reminder_kind", values_callable=lambda e: [m.value for m in e]),
        default=ReminderKind.REMINDER,
    )
    text: Mapped[str] = mapped_column(Text)
    cron: Mapped[str | None] = mapped_column(String(100))
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    local_date: Mapped[date] = mapped_column(Date)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation", "conversation_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[Any] = mapped_column(JSONB)
    source: Mapped[MessageSource] = mapped_column(
        Enum(MessageSource, name="message_source", values_callable=lambda e: [m.value for m in e])
    )
    tool_names: Mapped[list[str] | None] = mapped_column(ARRAY(String(64)))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
