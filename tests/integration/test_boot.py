from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine

from freddy.assistant.service import UserLocks
from freddy.boot import LAST_COMMIT, announce_boot
from freddy.db.engine import make_sessionmaker
from freddy.db.models import AppState, Message, User


class FakeChannel:
    def __init__(self) -> None:
        self.sent: list[str] = []

    @property
    def ready(self) -> bool:
        return True

    async def notify(self, text: str) -> None:
        self.sent.append(text)

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


async def test_boot_announces_and_remembers_commit(
    engine: AsyncEngine, committed_owner: User
) -> None:
    maker = make_sessionmaker(engine)
    async with maker() as s, s.begin():
        await s.execute(delete(AppState))
    channel = FakeChannel()

    await announce_boot(maker, channel, UserLocks(), 9001, "abc1234def")
    await announce_boot(maker, channel, UserLocks(), 9001, "abc1234def")

    assert channel.sent[0].endswith("Freshly updated to `abc1234`.")
    assert channel.sent[1].endswith("Just a restart, nothing new.")
    async with maker() as s:
        stored = await s.scalar(select(AppState.value).where(AppState.key == LAST_COMMIT))
        contents = list(await s.scalars(select(Message.content).where(Message.role == "user")))
    assert stored == "abc1234def"
    assert contents.count("(scheduled: boot)") == 2
