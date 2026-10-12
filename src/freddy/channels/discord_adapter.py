from collections.abc import Awaitable
from typing import Literal

import discord
import structlog
from discord import app_commands

from freddy.assistant.commands import SlashCommands
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
        commands: SlashCommands | None = None,
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
        self.tree = app_commands.CommandTree(self.client)
        self.tree.interaction_check = self._interaction_allowed  # type: ignore[method-assign]
        self.commands = commands
        if commands is not None:
            self._register(commands)

    @property
    def ready(self) -> bool:
        return self.client.is_ready() and not self.client.is_closed()

    async def start(self) -> None:
        await self.client.login(self.token)
        if self.commands is not None:
            synced = await self.tree.sync(guild=discord.Object(self.guild_id))
            log.info("discord.commands_synced", count=len(synced))
        await self.client.connect()

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

    def _interaction_ok(self, interaction: discord.Interaction) -> bool:
        return (
            interaction.user.id == self.owner_id
            and interaction.guild_id == self.guild_id
            and interaction.channel_id == self.channel_id
        )

    async def _interaction_allowed(self, interaction: discord.Interaction) -> bool:
        if self._interaction_ok(interaction):
            return True
        if interaction.type is discord.InteractionType.application_command:
            await interaction.response.send_message(
                "I only answer my owner, in the freddy channel.", ephemeral=True
            )
        return False

    def _register(self, commands: SlashCommands) -> None:
        guild = discord.Object(self.guild_id)

        @self.tree.command(name="goals", description="Today's goals", guild=guild)
        async def goals(interaction: discord.Interaction) -> None:
            await self._respond(interaction, commands.goals(interaction.user.id))

        @self.tree.command(name="done", description="Mark a goal done", guild=guild)
        @app_commands.describe(goal="Pick one of today's open goals")
        async def done(interaction: discord.Interaction, goal: int) -> None:
            await self._respond(interaction, commands.done(interaction.user.id, goal))

        @done.autocomplete("goal")
        async def done_goal(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[int]]:
            open_goals = await commands.open_goals(interaction.user.id, str(current))
            return [
                app_commands.Choice(name=f"#{gid} {title}"[:100], value=gid)
                for gid, title in open_goals[:25]
            ]

        @self.tree.command(name="remind", description="Set a reminder", guild=guild)
        @app_commands.describe(
            when="in 20 min, at 6pm, tomorrow at 9:30, every weekday at 7:30",
            what="What to remind you about",
        )
        async def remind(interaction: discord.Interaction, when: str, what: str) -> None:
            await self._respond(interaction, commands.remind(interaction.user.id, when, what))

        @self.tree.command(name="reminders", description="Active reminders", guild=guild)
        async def reminders(interaction: discord.Interaction) -> None:
            await self._respond(interaction, commands.reminders(interaction.user.id))

        @self.tree.command(name="checkin", description="Run a check-in now", guild=guild)
        @app_commands.describe(which="Defaults to whichever fits the time of day")
        async def checkin(
            interaction: discord.Interaction,
            which: Literal["morning", "evening"] | None = None,
        ) -> None:
            await self._respond(interaction, commands.checkin(interaction.user.id, which))

    async def _respond(self, interaction: discord.Interaction, reply: Awaitable[str]) -> None:
        await interaction.response.defer(thinking=True)
        parts = chunk(await reply) or ["👍"]
        for part in parts:
            await interaction.followup.send(part)

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
