import asyncio
import os
import re
from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from freddy.config import Settings
from freddy.db.models import Message, MessageSource

SIMPLE_TOOLS = {
    "add_goal",
    "list_goals",
    "complete_goal",
    "set_reminder",
    "list_reminders",
    "cancel_reminder",
    "get_progress",
    "set_checkin_times",
}


def _text(content: object) -> str:
    raw = content if isinstance(content, str) else ""
    return raw.split("\n", 1)[-1].strip()


def _shape(text: str) -> str:
    text = text.lower()
    text = re.sub(r"\d+(:\d+)?\s*(am|pm)?", "<n>", text)
    return re.sub(r"\s+", " ", text)


async def main() -> None:
    url = Settings._asyncpg_url(os.environ["DATABASE_URL"])
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = await conn.execute(
            select(Message.content, Message.tool_names).where(
                Message.source == MessageSource.USER,
                func.cardinality(Message.tool_names) == 1,
            )
        )
        by_tool: dict[str, Counter[str]] = {}
        for content, tools in rows:
            tool = tools[0]
            if tool in SIMPLE_TOOLS:
                by_tool.setdefault(tool, Counter())[_shape(_text(content))] += 1
    await engine.dispose()

    if not by_tool:
        print("nothing yet")
        return
    for tool, counter in sorted(by_tool.items(), key=lambda kv: -sum(kv[1].values())):
        print(f"\n{tool} ({sum(counter.values())} messages)")
        for shape, n in counter.most_common(10):
            print(f"  {n:>3}  {shape}")


if __name__ == "__main__":
    asyncio.run(main())
