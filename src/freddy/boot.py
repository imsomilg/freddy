import re

import aiohttp
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from freddy import persona
from freddy.assistant.history import ConversationStore
from freddy.assistant.service import UserLocks
from freddy.channels.base import Channel
from freddy.db.models import AppState, MessageSource, User

log = structlog.get_logger()

LAST_COMMIT = "last_boot_commit"
MAX_TITLES = 5
GITHUB_API = "https://api.github.com"

_TYPE_PREFIX = re.compile(r"^\w+(\([^)]*\))?!?:\s*")
_PR_SUFFIX = re.compile(r"\s*\(#\d+\)$")


def tidy(message: str) -> str:
    title = message.strip().splitlines()[0] if message.strip() else ""
    return _PR_SUFFIX.sub("", _TYPE_PREFIX.sub("", title.strip()))


def render_boot(greeting: str, commit: str | None, previous: str | None, titles: list[str]) -> str:
    if commit is None:
        return greeting
    if commit == previous:
        return f"{greeting} Just a restart, nothing new."
    if titles:
        return f"{greeting} Freshly updated:\n" + "\n".join(f"• {t}" for t in titles)
    return f"{greeting} Freshly updated to `{commit[:7]}`."


async def fetch_titles(
    repo: str, token: str | None, previous: str | None, commit: str
) -> list[str]:
    path = f"compare/{previous}...{commit}" if previous else f"commits/{commit}"
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as http,
            http.get(f"{GITHUB_API}/repos/{repo}/{path}", headers=headers) as resp,
        ):
            resp.raise_for_status()
            data = await resp.json()
    except (aiohttp.ClientError, TimeoutError) as e:
        log.warning("boot.github_failed", error=str(e))
        return []
    commits = data.get("commits", []) if previous else [data]
    messages = [c["commit"]["message"] for c in commits]
    titles = [tidy(m) for m in messages if not m.startswith("Merge ")]
    return [t for t in titles if t][-MAX_TITLES:]


async def announce_boot(
    sessionmaker: async_sessionmaker[AsyncSession],
    channel: Channel,
    locks: UserLocks,
    owner_discord_id: int,
    commit: str | None,
    repo: str | None = None,
    token: str | None = None,
) -> None:
    try:
        async with sessionmaker() as session:
            previous = await session.scalar(
                select(AppState.value).where(AppState.key == LAST_COMMIT)
            )
        titles: list[str] = []
        if commit and repo and commit != previous:
            titles = await fetch_titles(repo, token, previous, commit)
        text = render_boot(persona.line("boot"), commit, previous, titles)

        async with locks.get(owner_discord_id), sessionmaker() as session, session.begin():
            await channel.notify(text)
            user = await session.scalar(select(User).where(User.discord_id == owner_discord_id))
            if user is not None:
                await ConversationStore(session, user).append_exchange(
                    "(scheduled: boot)", text, MessageSource.SCHEDULER
                )
            if commit:
                await session.merge(AppState(key=LAST_COMMIT, value=commit))
        log.info("boot.announced", commit=commit, previous=previous, titles=len(titles))
    except Exception:
        log.exception("boot.announce_failed")
