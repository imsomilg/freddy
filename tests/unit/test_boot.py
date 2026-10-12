from freddy.boot import render_boot, tidy

SHA = "ca52bd2f0e1d"


def test_tidy_strips_type_and_pr_number() -> None:
    assert tidy("feat(scheduler): add weekly summary on sunday (#16)\n\nbody") == (
        "add weekly summary on sunday"
    )
    assert tidy("fix!: boot loop") == "boot loop"
    assert tidy("plain title") == "plain title"
    assert tidy("") == ""


def test_render_boot() -> None:
    hi = "At your service, sir."
    assert render_boot(hi, None, None, []) == hi
    assert render_boot(hi, SHA, SHA, []) == f"{hi} Just a restart, nothing new."
    assert render_boot(hi, SHA, "old", []) == f"{hi} Freshly updated to `ca52bd2`."
    assert render_boot(hi, SHA, "old", ["a", "b"]) == f"{hi} Freshly updated:\n• a\n• b"
