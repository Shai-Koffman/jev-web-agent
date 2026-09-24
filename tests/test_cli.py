import io
from pathlib import Path

import pytest
from rich.console import Console

from jev_web_agent.cli import TASKS, main, parse_args
from jev_web_agent.decide import quoted_literals
from jev_web_agent.human import TerminalHuman


@pytest.mark.parametrize("task", ["wiki", "hn", "books"])
def test_starter_tasks_resolve_to_url_and_goal(task: str) -> None:
    args = parse_args(["--task", task])

    assert args.url == TASKS[task].url
    assert args.goal == TASKS[task].goal
    assert args.headless is False  # headed by default
    assert args.max_steps == 15


def test_wiki_goal_quotes_the_text_to_type() -> None:
    assert quoted_literals(TASKS["wiki"].goal) == ["Alan Turing"]


def test_goal_needs_url_and_task_excludes_goal() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--goal", "x"])
    with pytest.raises(SystemExit):
        parse_args(["--task", "wiki", "--goal", "x"])
    with pytest.raises(SystemExit):
        parse_args(["--task", "wiki", "--url", "https://x"])


def test_thresholds_are_flags() -> None:
    args = parse_args(
        [
            "--task",
            "hn",
            "--min-prob",
            "0.7",
            "--min-conf",
            "0.5",
            "--done-threshold",
            "0.9",
            "--risky-threshold",
            "0.1",
        ]
    )

    assert (args.min_prob, args.min_conf, args.done_threshold, args.risky_threshold) == (
        0.7,
        0.5,
        0.9,
        0.1,
    )


def test_missing_api_key_exits_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)

    code = main(["--task", "wiki", "--headless", "--runs-dir", str(tmp_path / "runs")])

    assert code == 2
    assert "TYPESAFE_API_KEY is not set" in capsys.readouterr().err
    assert not (tmp_path / "runs").exists()  # failed before opening a browser


def test_terminal_human_picks_from_the_top_three() -> None:
    out = io.StringIO()
    human = TerminalHuman(Console(file=out, width=120), stream=io.StringIO("2\n"))

    picked = human.pick(
        "target",
        "low confidence on target",
        [("e3", 0.41, 'e3: link "A"'), ("e7", 0.33, 'e7: link "B"'), ("e1", 0.1, 'e1: link "C"')],
    )

    assert picked == "e7"
    assert "0.41" in out.getvalue() and 'e3: link "A"' in out.getvalue()


def test_terminal_human_can_abort_and_decline_a_block() -> None:
    human = TerminalHuman(Console(file=io.StringIO()), stream=io.StringIO("a\na\n"))

    assert human.pick("operation", "loop", [("click", 0.9, "")]) is None
    assert human.resolve_blocked('click [e1: button "Buy now"]') is False


def test_terminal_human_can_handle_a_block() -> None:
    human = TerminalHuman(Console(file=io.StringIO()), stream=io.StringIO("c\n"))

    assert human.resolve_blocked('click [e1: button "Buy now"]') is True
