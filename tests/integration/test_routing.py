from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from freddy.assistant.service import OFFLINE_REPLY, TIMEOUT_REPLY, Assistant, UserLocks
from freddy.bridge.hub import BridgeHub, BridgeOffline, BridgeTimeout
from freddy.db.engine import make_sessionmaker
from freddy.db.models import Message, MessageSource, User


class FakeHub(BridgeHub):
    def __init__(self, online: bool, outcome: str | Exception = "laptop says hi") -> None:
        super().__init__("t")
        self._online = online
        self.outcome = outcome
        self.prompts: list[str] = []

    @property
    def online(self) -> bool:
        return self._online

    async def ask(self, prompt: str, system: str) -> str:
        self.prompts.append(prompt)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.fixture
async def maker(engine: AsyncEngine) -> AsyncIterator[object]:
    yield make_sessionmaker(engine)


async def sources(engine: AsyncEngine, owner: User) -> list[MessageSource]:
    from freddy.db.models import Conversation

    async with make_sessionmaker(engine)() as s:
        rows = await s.scalars(
            select(Message.source)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Conversation.user_id == owner.id)
            .order_by(Message.id)
        )
        return list(rows)


async def test_offline_without_agent(engine: AsyncEngine, committed_owner: User) -> None:
    a = Assistant(make_sessionmaker(engine), UserLocks(), bridge=FakeHub(online=False))
    assert await a.handle(9001, "what should I focus on?") == OFFLINE_REPLY


async def test_fastpath_still_works_offline(engine: AsyncEngine, committed_owner: User) -> None:
    a = Assistant(make_sessionmaker(engine), UserLocks(), bridge=FakeHub(online=False))
    reply = await a.handle(9001, "add goal: stretch")
    assert reply.startswith("✅ Goal added")


async def test_bridge_reply_is_saved(engine: AsyncEngine, committed_owner: User) -> None:
    hub = FakeHub(online=True)
    a = Assistant(make_sessionmaker(engine), UserLocks(), bridge=hub)
    await a.handle(9001, "add goal: stretch")
    assert await a.handle(9001, "how's my day looking?") == "laptop says hi"
    assert "Earlier today" in hub.prompts[0]
    assert "how's my day looking?" in hub.prompts[0]
    assert await sources(engine, committed_owner) == [
        MessageSource.FASTPATH,
        MessageSource.FASTPATH,
        MessageSource.BRIDGE,
        MessageSource.BRIDGE,
    ]


async def test_bridge_timeout_reply(engine: AsyncEngine, committed_owner: User) -> None:
    a = Assistant(
        make_sessionmaker(engine), UserLocks(), bridge=FakeHub(online=True, outcome=BridgeTimeout())
    )
    assert await a.handle(9001, "plan my week") == TIMEOUT_REPLY
    assert await sources(engine, committed_owner) == []


async def test_bridge_vanishes_falls_back(engine: AsyncEngine, committed_owner: User) -> None:
    a = Assistant(
        make_sessionmaker(engine), UserLocks(), bridge=FakeHub(online=True, outcome=BridgeOffline())
    )
    assert await a.handle(9001, "plan my week") == OFFLINE_REPLY
