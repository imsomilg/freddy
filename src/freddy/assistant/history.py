from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from freddy.db.models import Conversation, Message, MessageSource, User
from freddy.domain.timeutil import local_today

MAX_MESSAGES_PER_CONVERSATION = 80


@dataclass
class NewMessage:
    role: str
    content: Any
    source: MessageSource
    tool_names: list[str] | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class ConversationStore:
    session: AsyncSession
    user: User
    _conversation: Conversation | None = field(default=None, init=False)

    async def current(self) -> Conversation:
        if self._conversation is not None:
            return self._conversation
        today = local_today(ZoneInfo(self.user.timezone))
        conv = await self.session.scalar(
            select(Conversation)
            .where(Conversation.user_id == self.user.id, Conversation.local_date == today)
            .order_by(Conversation.id.desc())
            .limit(1)
        )
        if conv is None or conv.message_count >= MAX_MESSAGES_PER_CONVERSATION:
            conv = Conversation(user_id=self.user.id, local_date=today, message_count=0)
            self.session.add(conv)
            await self.session.flush()
        self._conversation = conv
        return conv

    async def load(self) -> list[dict[str, Any]]:
        conv = await self.current()
        rows = await self.session.scalars(
            select(Message).where(Message.conversation_id == conv.id).order_by(Message.id)
        )
        return [{"role": m.role, "content": m.content} for m in rows]

    async def append(self, messages: list[NewMessage]) -> None:
        conv = await self.current()
        for m in messages:
            self.session.add(
                Message(
                    conversation_id=conv.id,
                    role=m.role,
                    content=m.content,
                    source=m.source,
                    tool_names=m.tool_names,
                    input_tokens=m.input_tokens,
                    output_tokens=m.output_tokens,
                )
            )
        conv.message_count += len(messages)
        await self.session.flush()

    async def append_exchange(
        self,
        user_text: str,
        reply: str,
        source: MessageSource,
        tool_names: list[str] | None = None,
    ) -> None:
        await self.append(
            [
                NewMessage("user", user_text, source, tool_names=tool_names),
                NewMessage("assistant", [{"type": "text", "text": reply}], source),
            ]
        )
