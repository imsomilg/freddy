from collections.abc import Awaitable, Callable
from typing import Protocol

MessageHandler = Callable[[int, str], Awaitable[str]]


class Channel(Protocol):
    @property
    def ready(self) -> bool: ...

    async def notify(self, text: str) -> None: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


def chunk(text: str, limit: int = 2000) -> list[str]:
    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks
