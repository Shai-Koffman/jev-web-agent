"""The whole loop, wired exactly as the CLI wires it, with a scripted fake Jev injected."""

import io
from pathlib import Path

import pytest
from rich.console import Console

from jev_web_agent.cli import run
from jev_web_agent.report import RunRecord
from tests.fakes import ScriptedHuman, ScriptedJev, Thought


def _run(
    tmp_path: Path,
    url: str,
    goal: str,
    jev: ScriptedJev,
    *extra: str,
    human: ScriptedHuman | None = None,
) -> tuple[RunRecord, Path, str]:
    out = io.StringIO()
    argv = ["--url", url, "--goal", goal, "--headless", "--runs-dir", str(tmp_path), *extra]
    if human is None:
        argv.append("--no-ask")
    record, run_dir = run(argv, jev=jev, human=human, console=Console(file=out, width=200))
    return record, run_dir, out.getvalue()


def _executed(record: RunRecord) -> list[str]:
    return [s.action for s in record.steps if s.action and "gone" not in s.result]


def test_search_open_article_done(tmp_path: Path, site: str) -> None:
    jev = ScriptedJev(
        [
            Thought("type", target="Search encyclopedia", text="Alan Turing"),
            Thought("press_enter"),
            Thought("click", target='link "Alan Turing"'),
            Thought("done", goal_done=0.95),
        ]
    )

    record, run_dir, out = _run(
        tmp_path, f"{site}/search.html", 'search for "Alan Turing" and open his article', jev
    )

    assert record.status == "done"
    assert [s.url for s in record.steps] == [
        f"{site}/search.html",
        f"{site}/search.html",
        f"{site}/results.html?q=Alan+Turing",
        f"{site}/article.html",
    ]
    assert len(jev.calls) == 4  # exactly one Jev call per step
    last_state = jev.calls[-1][0]
    assert 'type [e1: searchbox "Search encyclopedia" value=""] text="Alan Turing"' in last_state
    assert "press_enter on" in last_state
    for n in range(1, 5):
        assert (run_dir / f"step-{n:02d}.png").stat().st_size > 0
    html = (run_dir / "report.html").read_text()
    assert '<img src="step-04.png"' in html
    assert (run_dir / "run.json").exists()
    assert "step 1" in out and "type" in out and "PASS" in out and "DONE" in out


def test_low_confidence_without_a_human_aborts_before_acting(tmp_path: Path, site: str) -> None:
    jev = ScriptedJev([Thought("click", target="Turing machine", prob=0.4)])

    record, _, out = _run(tmp_path, f"{site}/results.html", "open the article", jev)

    assert record.status == "aborted"
    assert "low confidence" in record.reason
    assert _executed(record) == []
    assert len(record.steps) == 1
    assert "ASK" in out


def test_low_confidence_asks_the_human_who_picks(tmp_path: Path, site: str) -> None:
    jev = ScriptedJev(
        [Thought("click", target="Turing machine", prob=0.4), Thought("done", goal_done=0.9)]
    )
    human = ScriptedHuman(picks={"operation": "click", "target": "e2"})

    record, _, _ = _run(tmp_path, f"{site}/results.html", "open the article", jev, human=human)

    assert human.asked == ["operation", "target"]
    assert record.status == "done"
    assert record.steps[1].url == f"{site}/article.html"
    assert record.steps[0].human == 'picked click [e2: link "Alan Turing"]'


def test_risky_step_is_blocked_and_never_executed(tmp_path: Path, site: str) -> None:
    jev = ScriptedJev([Thought("click", target="Buy now", risky=0.7)])

    record, _, out = _run(tmp_path, f"{site}/shop.html", 'buy "A Light in the Attic"', jev)

    assert record.status == "blocked"
    assert "Buy now" in record.reason
    assert _executed(record) == []
    assert record.steps[0].title != "PURCHASED"
    assert len(record.steps) == 1
    assert "BLOCK" in out


def test_risky_step_handled_by_the_human_is_still_not_executed(tmp_path: Path, site: str) -> None:
    jev = ScriptedJev(
        [Thought("click", target="Buy now", risky=0.7), Thought("done", goal_done=0.9)]
    )
    human = ScriptedHuman(handle_blocked=True)

    record, _, _ = _run(tmp_path, f"{site}/shop.html", "buy it", jev, human=human)

    assert human.asked == ["blocked"]
    assert record.status == "done"
    assert _executed(record) == []
    assert [s.title for s in record.steps] == ["A Light in the Attic | Books"] * 2


def test_same_action_three_times_in_a_row_asks(tmp_path: Path, site: str) -> None:
    jev = ScriptedJev([Thought("scroll_down")])

    record, _, _ = _run(tmp_path, f"{site}/many.html", "find the last link", jev)

    assert record.status == "aborted"
    assert record.reason.startswith("loop")
    assert _executed(record) == ["scroll_down", "scroll_down"]
    assert len(record.steps) == 3


def test_loop_ask_lets_the_human_choose(tmp_path: Path, site: str) -> None:
    jev = ScriptedJev(
        [
            Thought("scroll_down"),
            Thought("scroll_down"),
            Thought("scroll_down"),
            Thought("done", goal_done=0.9),
        ]
    )
    human = ScriptedHuman(picks={"operation": "scroll_down"})

    record, _, _ = _run(tmp_path, f"{site}/many.html", "find the last link", jev, human=human)

    assert human.asked == ["operation"]
    assert record.status == "done"
    assert _executed(record) == ["scroll_down"] * 3


def test_goal_done_finishes_without_acting(tmp_path: Path, site: str) -> None:
    jev = ScriptedJev([Thought("click", target="Back to search", goal_done=0.85)])

    record, _, _ = _run(tmp_path, f"{site}/article.html", "open the article", jev)

    assert record.status == "done"
    assert _executed(record) == []


def test_done_operation_finishes(tmp_path: Path, site: str) -> None:
    jev = ScriptedJev([Thought("done", goal_done=0.3)])

    record, _, _ = _run(tmp_path, f"{site}/article.html", "open the article", jev)

    assert record.status == "done"
    assert "operation 'done'" in record.reason


def test_element_gone_before_acting_reobserves(tmp_path: Path, site: str) -> None:
    """vanish-later.html removes its button 200ms after the agent tags it; the fake Jev
    'thinks' for 1s, so the button is gone by the time the agent would click."""
    jev = ScriptedJev(
        [Thought("click", target="Ghost button"), Thought("done", goal_done=0.9)],
        think_seconds=1.0,
    )

    record, _, out = _run(tmp_path, f"{site}/vanish-later.html", "click the ghost", jev)

    assert record.steps[0].result.endswith("is gone; re-observing")
    assert record.steps[1].title == "Vanishing button"  # never clicked
    assert "Ghost button" not in "\n".join(record.steps[1].elements)
    assert record.status == "done"
    assert "gone" in out


def test_max_steps_stops_the_run(tmp_path: Path, site: str) -> None:
    jev = ScriptedJev([Thought("scroll_down"), Thought("go_back")])

    record, _, _ = _run(tmp_path, f"{site}/many.html", "wander", jev, "--max-steps", "1")

    assert record.status == "max_steps"
    assert len(record.steps) == 1


def test_the_api_key_never_reaches_output(
    tmp_path: Path, site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "sk-test-DO-NOT-PRINT-4242"
    monkeypatch.setenv("TYPESAFE_API_KEY", secret)
    jev = ScriptedJev([Thought("done", goal_done=0.9)])

    _, run_dir, out = _run(tmp_path, f"{site}/article.html", "open the article", jev)

    assert secret not in out
    for path in run_dir.iterdir():
        if path.suffix in (".html", ".json"):
            assert secret not in path.read_text()
