from itertools import pairwise

from freddy import persona

DASHES = ("\N{EM DASH}", "\N{EN DASH}")


def test_every_line_says_sir_and_has_no_dashes() -> None:
    for kind, pool in persona.LINES.items():
        assert pool, kind
        for text in pool:
            assert "sir" in text.lower(), text
            assert not any(d in text for d in DASHES), text


def test_line_never_repeats_back_to_back() -> None:
    picks = [persona.line("goal_added") for _ in range(50)]
    assert all(a != b for a, b in pairwise(picks))
    assert set(picks) <= set(persona.LINES["goal_added"])
