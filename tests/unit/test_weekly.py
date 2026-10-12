from datetime import date, time, timedelta

from freddy.domain.goals import Progress
from freddy.domain.timeutil import weekly_cron
from freddy.scheduler.loop import render_weekly

MON = date(2026, 10, 5)


def week(*counts: tuple[int, int], streak: int = 0) -> Progress:
    per_day = [(MON + timedelta(days=i), d, t) for i, (d, t) in enumerate(counts)]
    return Progress(
        days=len(per_day),
        done=sum(d for _, d, _ in per_day),
        total=sum(t for _, _, t in per_day),
        streak=streak,
        per_day=per_day,
    )


def test_weekly_skipped_without_goals() -> None:
    assert render_weekly(week(*[(0, 0)] * 7)) is None


def test_weekly_summary() -> None:
    text = render_weekly(week((1, 2), (3, 3), (0, 1), (0, 0), (2, 2), (1, 1), (0, 0), streak=3))
    assert text is not None
    assert "7 of 9 goals done (78%)" in text
    assert "Best day: Tuesday with 3 done." in text
    assert "Streak: 3 days" in text
    assert "Mon 1/2 · Tue 3/3" in text
    assert "next week" in text


def test_weekly_no_done_no_best_day() -> None:
    text = render_weekly(week((0, 2), *[(0, 0)] * 6))
    assert text is not None
    assert "0 of 2" in text
    assert "Best day" not in text
    assert "Streak" not in text


def test_weekly_clean_sweep() -> None:
    text = render_weekly(week((2, 2), (1, 1), *[(0, 0)] * 5, streak=2))
    assert text is not None
    assert "Clean sweep" in text


def test_weekly_cron() -> None:
    assert weekly_cron(time(21, 0), 30) == "30 21 * * 0"
    assert weekly_cron(time(23, 45), 30) == "15 0 * * 1"
    assert weekly_cron(time(9, 0)) == "0 9 * * 0"
