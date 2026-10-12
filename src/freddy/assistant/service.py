import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from freddy.assistant.agent import Agent, AgentError
from freddy.assistant.history import ConversationStore
from freddy.assistant.prompts import SYSTEM_PROMPT, now_line
from freddy.assistant.tools import ToolContext
from freddy.bridge.hub import BridgeFailed, BridgeHub, BridgeOffline, BridgeTimeout
from freddy.bridge.prompt import build_bridge_prompt
from freddy.db.models import MessageSource, User
from freddy.domain.timeutil import utcnow
from freddy.fastpath.router import try_fastpath
from freddy.integrations.google.calendar import CalendarService

log = structlog.get_logger()

OFFLINE_REPLY = (
    "My apologies, sir, my brain is offline (the laptop's off). I can still manage:\n"
    "`add goal: X`, `goals`, `today`, `what's on tomorrow`, `done X`, `reminders`, "
    "`cancel reminder 3`, "
    "`remind me in 20 min / at 6pm / every weekday at 7:30 to X`"
)
TIMEOUT_REPLY = "That one took me far too long, sir. Shall we try again?"
FAILED_REPLY = "My brain on the laptop stumbled on that one, sir. Try again in a moment?"


class UserLocks:
    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}

    def get(self, user_id: int) -> asyncio.Lock:
        return self._locks.setdefault(user_id, asyncio.Lock())


def stamp(text: str, now: datetime, tz: ZoneInfo) -> str:
    local = now.astimezone(tz)
    return f"{now_line(local.strftime('%a %Y-%m-%d %H:%M'), str(tz))}\n{text}"


class Assistant:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        locks: UserLocks,
        agent: Agent | None = None,
        bridge: BridgeHub | None = None,
        calendar: CalendarService | None = None,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.locks = locks
        self.agent = agent
        self.bridge = bridge
        self.calendar = calendar

    async def handle(self, discord_id: int, text: str) -> str:
        async with self.locks.get(discord_id):
            try:
                return await self._handle(discord_id, text)
            except AgentError as e:
                return e.user_message
            except Exception:
                log.exception("assistant.failed")
                return "Something broke on my side, sir. My apologies. The logs will know more."

    async def _handle(self, discord_id: int, text: str) -> str:
        now = utcnow()
        async with self.sessionmaker() as session, session.begin():
            user = await session.scalar(select(User).where(User.discord_id == discord_id))
            if user is None:
                return "I don't believe we've been introduced."
            content = stamp(text, now, ZoneInfo(user.timezone))
            store = ConversationStore(session, user)

            fast = await try_fastpath(text, session, user, now, self.calendar)
            if fast is not None:
                log.info("fastpath.hit", intent=fast.intent)
                await store.append_exchange(
                    content,
                    fast.reply,
                    MessageSource.FASTPATH,
                    tool_names=[f"fastpath:{fast.intent}"],
                )
                return fast.reply
            history = await store.load()

        if self.bridge is not None and self.bridge.online:
            reply = await self._via_bridge(discord_id, history, content)
            if reply is not None:
                return reply

        if self.agent is not None:
            return await self._via_agent(discord_id, content)
        return OFFLINE_REPLY

    async def _via_bridge(
        self, discord_id: int, history: list[dict[str, object]], content: str
    ) -> str | None:
        assert self.bridge is not None
        try:
            reply = await self.bridge.ask(build_bridge_prompt(history, content), SYSTEM_PROMPT)
        except BridgeOffline:
            log.info("bridge.offline_fallback")
            return None
        except BridgeTimeout:
            log.warning("bridge.timeout")
            return TIMEOUT_REPLY
        except BridgeFailed as e:
            log.warning("bridge.failed", error=str(e))
            return FAILED_REPLY
        reply = reply.strip() or "👍"
        async with self.sessionmaker() as session, session.begin():
            user = await session.scalar(select(User).where(User.discord_id == discord_id))
            assert user is not None
            await ConversationStore(session, user).append_exchange(
                content, reply, MessageSource.BRIDGE
            )
        log.info("bridge.replied")
        return reply

    async def _via_agent(self, discord_id: int, content: str) -> str:
        assert self.agent is not None
        async with self.sessionmaker() as session, session.begin():
            user = await session.scalar(select(User).where(User.discord_id == discord_id))
            assert user is not None
            store = ConversationStore(session, user)
            history = await store.load()
            result = await self.agent.run(
                history, content, ToolContext(session, user, self.calendar)
            )
            await store.append(result.messages)
            return result.reply
