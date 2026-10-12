import asyncio
import signal

import anthropic
import structlog

from freddy.assistant.agent import Agent
from freddy.assistant.commands import SlashCommands
from freddy.assistant.service import Assistant, UserLocks
from freddy.bridge.hub import BridgeHub
from freddy.channels.discord_adapter import DiscordChannel
from freddy.config import get_settings
from freddy.db.engine import make_engine, make_sessionmaker
from freddy.domain.users import ensure_owner
from freddy.logging import setup_logging
from freddy.mcp_server import build_mcp_app
from freddy.scheduler.loop import Scheduler
from freddy.web import build_app, make_server

log = structlog.get_logger()


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_json)

    engine = make_engine(settings.database_url)
    sessionmaker = make_sessionmaker(engine)
    async with sessionmaker() as session, session.begin():
        await ensure_owner(
            session,
            settings.owner_discord_id,
            settings.timezone,
            settings.morning_checkin,
            settings.evening_checkin,
            settings.weekly_summary_delay,
        )

    agent = None
    if settings.anthropic_api_key:
        agent = Agent(
            anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value()),
            model=settings.claude_model,
            effort=settings.claude_effort,
            max_tokens=settings.claude_max_tokens,
            max_tool_rounds=settings.claude_max_tool_rounds,
        )
    hub = (
        BridgeHub(settings.bridge_token.get_secret_value(), timeout=settings.bridge_timeout)
        if settings.bridge_token
        else None
    )
    mcp = (
        build_mcp_app(
            sessionmaker, settings.owner_discord_id, settings.mcp_token.get_secret_value()
        )
        if settings.mcp_token
        else None
    )
    if hub and not mcp:
        log.warning("bridge.no_mcp_token", hint="laptop Claude won't have Freddy's tools")

    locks = UserLocks()
    assistant = Assistant(sessionmaker, locks, agent=agent, bridge=hub)
    channel = DiscordChannel(
        token=settings.discord_token.get_secret_value(),
        guild_id=settings.discord_guild_id,
        channel_id=settings.discord_channel_id,
        owner_id=settings.owner_discord_id,
        handler=assistant.handle,
        commands=SlashCommands(sessionmaker, locks),
    )
    scheduler = Scheduler(sessionmaker, channel, locks)
    web = make_server(build_app(channel, hub, mcp), settings.health_port)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    log.info(
        "freddy.starting",
        timezone=settings.timezone,
        api_agent=agent is not None,
        bridge=hub is not None,
        mcp=mcp is not None,
    )
    tasks = [
        asyncio.create_task(channel.start(), name="discord"),
        asyncio.create_task(scheduler.run(), name="scheduler"),
        asyncio.create_task(web.serve(), name="web"),
        asyncio.create_task(stop.wait(), name="stop"),
    ]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task.get_name() != "stop" and task.exception():
                log.error("freddy.task_crashed", task=task.get_name(), exc_info=task.exception())
    finally:
        log.info("freddy.stopping")
        web.should_exit = True
        for task in tasks:
            if task.get_name() != "web":
                task.cancel()
        await channel.stop()
        await asyncio.gather(*tasks, return_exceptions=True)
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
