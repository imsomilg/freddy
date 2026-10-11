import discord
import structlog

from freddy.channels.base import MessageHandler, chunk

log = structlog.get_logger()

MENTION_ONLY_USERS = discord.AllowedMentions(everyone=False, roles=False, users=True)
NO_MENTIONS = discord.AllowedMentions.none()


class DiscordChannel:
    def __init__(
        self,
        token: str,
        guild_id: int,
        channel_id: int,
        owner_id: int,
        handler: MessageHandler,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        self.client = discord.Client(intents=intents, allowed_mentions=NO_MENTIONS)
        self.token = token
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.owner_id = owner_id
        self.handler = handler
        self.client.event(self.on_ready)
        self.client.event(self.on_message)

    @property
    def ready(self) -> bool:
        return self.client.is_ready() and not self.client.is_closed()

    async def start(self) -> None:
        await self.client.start(self.token)

    async def stop(self) -> None:
        await self.client.close()

    async def on_ready(self) -> None:
        log.info("discord.ready", user=str(self.client.user), guilds=len(self.client.guilds))
        if self.client.get_guild(self.guild_id) is None:
            log.error("discord.guild_missing", guild_id=self.guild_id)

    def _allowed(self, message: discord.Message) -> bool:
        return (
            not message.author.bot
            and message.author.id == self.owner_id
            and message.guild is not None
            and message.guild.id == self.guild_id
            and message.channel.id == self.channel_id
        )

    async def on_message(self, message: discord.Message) -> None:
        if not self._allowed(message):
            return
        text = message.content.strip()
        if not text:
            return
        async with message.channel.typing():
            reply = await self.handler(message.author.id, text)
        parts = chunk(reply)
        if not parts:
            return
        await message.reply(parts[0], mention_author=False)
        for part in parts[1:]:
            await message.channel.send(part)

    async def _channel(self) -> discord.abc.Messageable:
        channel = self.client.get_channel(self.channel_id)
        if channel is None:
            channel = await self.client.fetch_channel(self.channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            raise RuntimeError(f"channel {self.channel_id} can't receive messages")
        return channel

    async def notify(self, text: str) -> None:
        await self.client.wait_until_ready()
        channel = await self._channel()
        for i, part in enumerate(chunk(text, 1900)):
            content = f"<@{self.owner_id}> {part}" if i == 0 else part
            await channel.send(content, allowed_mentions=MENTION_ONLY_USERS)
