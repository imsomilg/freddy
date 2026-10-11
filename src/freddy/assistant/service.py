import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from freddy.assistant.agent import Agent, AgentError
from freddy.assistant.history import ConversationStore
from freddy.assistant.prompts import now_line
from freddy.assistant.tools import ToolContext
from freddy.db.models import MessageSource, User
from freddy.domain.timeutil import utcnow
from freddy.fastpath.router import try_fastpath

log = structlog.get_logger()


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
        self, sessionmaker: async_sessionmaker[AsyncSession], agent: Agent, locks: UserLocks
    ) -> None:
        self.sessionmaker = sessionmaker
        self.agent = agent
        self.locks = locks

    async def handle(self, discord_id: int, text: str) -> str:
        async with self.locks.get(discord_id):
            try:
                return await self._handle(discord_id, text)
            except AgentError as e:
                return e.user_message
            except Exception:
                log.exception("assistant.failed")
                return "Something broke on my side. Check the logs."

    async def _handle(self, discord_id: int, text: str) -> str:
        now = utcnow()
        async with self.sessionmaker() as session, session.begin():
            user = await session.scalar(select(User).where(User.discord_id == discord_id))
            if user is None:
                return "I don't know you yet."
            tz = ZoneInfo(user.timezone)
            store = ConversationStore(session, user)
            content = stamp(text, now, tz)

            fast = await try_fastpath(text, session, user, now)
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
            result = await self.agent.run(history, content, ToolContext(session, user))
            await store.append(result.messages)
            return result.reply
