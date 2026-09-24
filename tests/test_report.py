import json
from pathlib import Path

from jev_web_agent.decide import NO_TEXT, ChoiceResult, Decision
from jev_web_agent.report import RunRecord, StepRecord, new_run_dir, write_report

# A 1x1 transparent PNG.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


def _decision() -> Decision:
    return Decision(
        operation=ChoiceResult("click", {"click": 0.91, "type": 0.06, "done": 0.03}, 0.87),
        target=ChoiceResult("e2", {"e1": 0.2, "e2": 0.8}, 0.6),
        text=ChoiceResult(NO_TEXT, {"Alan Turing": 0.1, NO_TEXT: 0.9}, 0.8),
        goal_done=0.04,
        risky=0.02,
        state="GOAL: <b>not bold</b>",
    )


def _run(run_dir: Path) -> RunRecord:
    (run_dir / "step-01.png").write_bytes(PNG)
    return RunRecord(
        goal='search for "Alan Turing"',
        start_url="http://127.0.0.1/search.html",
        started="24/09/2026 10:00:00",
        model="jev-latest",
        thresholds={"min_prob": 0.55, "min_conf": 0.35, "done": 0.8, "risky": 0.3},
        status="done",
        reason="goal_done=0.93 >= 0.8",
        steps=[
            StepRecord(
                number=1,
                url="http://127.0.0.1/results.html",
                title="Search results",
                screenshot="step-01.png",
                elements=['e1: link "<script>alert(1)</script>"', 'e2: link "Alan Turing"'],
                decision=_decision(),
                gate="pass",
                gate_reason="all relevant answers passed",
                human=None,
                action='click [e2: link "Alan Turing"]',
                result="clicked e2",
            ),
            StepRecord(
                number=2,
                url="http://127.0.0.1/article.html",
                title="Alan Turing",
                screenshot=None,
                elements=[],
                decision=None,
                gate="error",
                gate_reason="Jev call failed",
                human=None,
                action=None,
                result="",
            ),
        ],
    )


def test_writes_run_json_and_a_self_contained_report(tmp_path: Path) -> None:
    run = _run(tmp_path)

    report = write_report(tmp_path, run)

    assert report == tmp_path / "report.html"
    html = report.read_text()
    assert '<img src="step-01.png"' in html
    assert (tmp_path / "step-01.png").exists()
    assert "search for &quot;Alan Turing&quot;" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html  # element list, escaped
    assert "<script>alert" not in html
    assert "0.91" in html and "0.87" in html  # operation probability and confidence
    assert "e2" in html and "0.80" in html  # target answer
    assert "goal_done" in html and "0.04" in html
    assert "risky" in html and "0.02" in html
    assert "all relevant answers passed" in html
    assert "&lt;b&gt;not bold&lt;/b&gt;" in html  # the exact state sent to Jev
    assert "Jev call failed" in html
    assert "<link" not in html and "<script" not in html  # no external assets, no scripts

    data = json.loads((tmp_path / "run.json").read_text())
    assert data["status"] == "done"
    assert data["steps"][0]["decision"]["operation"]["probabilities"]["click"] == 0.91
    assert data["steps"][0]["decision"]["risky"] == 0.02
    assert data["steps"][1]["decision"] is None


def test_new_run_dir_is_timestamped_and_unique(tmp_path: Path) -> None:
    first = new_run_dir(tmp_path)
    second = new_run_dir(tmp_path)

    assert first.is_dir() and second.is_dir()
    assert first != second
    assert len(first.name) == len("2026-09-24_10-00-00")
    assert second.name.startswith(first.name)
