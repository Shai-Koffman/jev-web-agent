"""--backend selection, and the whole CLI on the OpenRouter backend with only the HTTP
transport replaced."""

import io
import json
from pathlib import Path
from typing import Any, cast

import httpx2
import pytest
from rich.console import Console

from jev_web_agent.cli import main, resolve_backend, run
from jev_web_agent.models import OPERATIONS

FAKE_KEY = "sk-or-test-NOT-A-REAL-KEY-5150"


def test_default_backend_is_openrouter_when_its_key_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert resolve_backend(None) == "typesafe"
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_KEY)
    assert resolve_backend(None) == "openrouter"
    assert resolve_backend("typesafe") == "typesafe"


def test_openrouter_backend_without_its_key_exits_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--task", "wiki", "--backend", "openrouter", "--runs-dir", str(tmp_path / "r")])

    assert code == 2
    assert "OPENROUTER_API_KEY is not set" in capsys.readouterr().err
    assert not (tmp_path / "r").exists()


def _answer(options: list[str], chosen: str) -> dict[str, Any]:
    rest = 0.1 / (len(options) - 1) if len(options) > 1 else 0.0
    probabilities = {o: (0.9 if o == chosen else rest) for o in options}
    if len(options) == 1:
        probabilities[chosen] = 1.0
    return {"type": "choice", "choice": chosen, "probabilities": probabilities, "confidence": 0.85}


def _fake_jev(request: httpx2.Request) -> httpx2.Response:
    """Plays Jev on the wire: click the 'Alan Turing' result, then say the goal is done."""
    body = cast(dict[str, Any], json.loads(request.content))
    state = cast(str, body["state"])
    questions = cast(dict[str, dict[str, Any]], body["questions"])
    on_article = "/article.html" in state.splitlines()[1]
    targets = cast(dict[str, str], questions["target"]["criteria"])
    link = next(
        (k for k, line in targets.items() if line.endswith('link "Alan Turing"')),
        next(iter(targets)),
    )
    answers: dict[str, Any] = {
        "operation": _answer(list(OPERATIONS), "done" if on_article else "click"),
        "target": _answer(list(targets), link),
        "goal_done": {"type": "noul", "noul": 0.95 if on_article else 0.02},
        "risky": {"type": "noul", "noul": 0.01},
    }
    return httpx2.Response(
        200,
        json={
            "id": "gen-dec-test",
            "model": "typesafe/jev-1.13-20260917",
            "provider": "TypeSafe",
            "answers": answers,
            "usage": {"input_tokens": 400, "output_tokens": 50, "cost": 0.0000168},
        },
    )


def test_cli_runs_on_the_openrouter_backend(
    tmp_path: Path, site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_KEY)
    out = io.StringIO()

    record, run_dir = run(
        [
            "--url",
            f"{site}/results.html",
            "--goal",
            "open the Alan Turing article",
            "--headless",
            "--no-ask",
            "--runs-dir",
            str(tmp_path),
        ],
        console=Console(file=out, width=200),
        transport=httpx2.MockTransport(_fake_jev),
    )

    assert record.status == "done", record.reason
    assert record.model == "typesafe/jev-1.13"
    assert [s.url for s in record.steps] == [f"{site}/results.html", f"{site}/article.html"]
    usage = json.loads((run_dir / "usage.json").read_text())
    assert usage["total"] == {
        "calls": 2,
        "latency_seconds": usage["total"]["latency_seconds"],
        "input_tokens": 800,
        "output_tokens": 100,
        "cost": pytest.approx(0.0000336),
    }
    assert "openrouter" in out.getvalue() and "800 input tokens" in out.getvalue()
    for path in [*run_dir.iterdir()]:
        if path.suffix in (".html", ".json"):
            assert FAKE_KEY not in path.read_text()
    assert FAKE_KEY not in out.getvalue()
