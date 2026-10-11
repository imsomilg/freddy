from dataclasses import dataclass
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from rapidfuzz import fuzz, process
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from freddy.db.models import Goal, GoalStatus, User
from freddy.domain.timeutil import local_today, utcnow


class GoalNotFound(Exception):
    pass


@dataclass(frozen=True)
class Progress:
    days: int
    done: int
    total: int
    streak: int
    per_day: list[tuple[date, int, int]]


class GoalService:
    def __init__(self, session: AsyncSession, user: User) -> None:
        self.session = session
        self.user = user
        self.tz = ZoneInfo(user.timezone)

    def today(self) -> date:
        return local_today(self.tz)

    async def add(self, title: str, for_date: date | None = None) -> Goal:
        goal = Goal(user_id=self.user.id, title=title.strip(), for_date=for_date or self.today())
        self.session.add(goal)
        await self.session.flush()
        return goal

    async def for_day(self, day: date | None = None) -> list[Goal]:
        day = day or self.today()
        stmt = select(Goal).where(Goal.user_id == self.user.id)
        if day == self.today():
            stmt = stmt.where(
                or_(
                    Goal.for_date == day,
                    (Goal.for_date < day) & (Goal.status == GoalStatus.OPEN),
                )
            )
        else:
            stmt = stmt.where(Goal.for_date == day)
        stmt = stmt.where(Goal.status != GoalStatus.DROPPED).order_by(Goal.for_date, Goal.id)
        return list(await self.session.scalars(stmt))

    async def open_today(self) -> list[Goal]:
        return [g for g in await self.for_day() if g.status == GoalStatus.OPEN]

    async def get(self, goal_id: int) -> Goal:
        goal = await self.session.get(Goal, goal_id)
        if goal is None or goal.user_id != self.user.id:
            raise GoalNotFound(goal_id)
        return goal

    async def complete(self, goal_id: int) -> Goal:
        goal = await self.get(goal_id)
        goal.status = GoalStatus.DONE
        goal.completed_at = utcnow()
        return goal

    async def update(
        self,
        goal_id: int,
        title: str | None = None,
        for_date: date | None = None,
        status: GoalStatus | None = None,
    ) -> Goal:
        goal = await self.get(goal_id)
        if title:
            goal.title = title.strip()
        if for_date:
            goal.for_date = for_date
        if status:
            goal.status = status
            goal.completed_at = utcnow() if status == GoalStatus.DONE else None
        return goal

    async def match_open(self, query: str, cutoff: float = 70) -> list[Goal]:
        goals = await self.open_today()
        if not goals:
            return []
        choices = {g.id: g.title.lower() for g in goals}
        hits = process.extract(
            query.lower(), choices, scorer=fuzz.WRatio, score_cutoff=cutoff, limit=5
        )
        if not hits:
            return []
        best = hits[0][1]
        by_id = {g.id: g for g in goals}
        return [by_id[key] for _, score, key in hits if best - score < 8]

    async def progress(self, days: int = 7) -> Progress:
        today = self.today()
        start = today - timedelta(days=days - 1)
        rows = await self.session.execute(
            select(
                Goal.for_date,
                func.count().filter(Goal.status == GoalStatus.DONE),
                func.count(),
            )
            .where(
                Goal.user_id == self.user.id,
                Goal.for_date >= start - timedelta(days=60),
                Goal.status != GoalStatus.DROPPED,
            )
            .group_by(Goal.for_date)
        )
        by_day = {d: (int(done), int(total)) for d, done, total in rows}
        per_day = [
            (d, *by_day.get(d, (0, 0))) for d in (start + timedelta(days=i) for i in range(days))
        ]
        streak = 0
        cursor = today if by_day.get(today, (0, 0))[0] > 0 else today - timedelta(days=1)
        while by_day.get(cursor, (0, 0))[0] > 0:
            streak += 1
            cursor -= timedelta(days=1)
        return Progress(
            days=days,
            done=sum(d for _, d, _ in per_day),
            total=sum(t for _, _, t in per_day),
            streak=streak,
            per_day=per_day,
        )


def format_goal(goal: Goal, today: date) -> str:
    mark = "✅" if goal.status == GoalStatus.DONE else "⬜"
    carried = f" (from {goal.for_date:%a})" if goal.for_date < today else ""
    return f"{mark} #{goal.id} {goal.title}{carried}"


def format_goal_list(goals: list[Goal], today: date) -> str:
    return "\n".join(format_goal(g, today) for g in goals)
