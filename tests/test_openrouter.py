"""OpenRouterJev: the real adapter, with only its HTTP transport replaced."""

import json
from pathlib import Path
from typing import Any, cast

import httpx2
import pytest

from jev_web_agent.decide import NO_TEXT, build_questions, decide
from jev_web_agent.models import OPERATIONS, Element, Observation
from jev_web_agent.observe import render_state
from jev_web_agent.openrouter import (
    OPENROUTER_MODEL,
    OPENROUTER_URL,
    OpenRouterError,
    OpenRouterJev,
)

FAKE_KEY = "sk-or-test-NOT-A-REAL-KEY-7731"

OBS = Observation(
    url="https://example.test/",
    title="Example",
    text="Hello",
    elements=(
        Element(id="e1", role="searchbox", name="Search", value=""),
        Element(id="e2", role="button", name="Go", value=None),
    ),
)


def _decisions_response(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "gen-dec-1-abc",
        "model": "typesafe/jev-1.13-20260917",
        "provider": "TypeSafe",
        "answers": {
            "operation": {
                "type": "choice",
                "choice": "type",
                "probabilities": dict.fromkeys(OPERATIONS, 0.02) | {"type": 0.9},
                "confidence": 0.87,
            },
            "target": {
                "type": "choice",
                "choice": "e1",
                "probabilities": {"e1": 0.95, "e2": 0.05},
                "confidence": 0.9,
            },
            "text": {
                "type": "choice",
                "choice": "cats",
                "probabilities": {"cats": 0.8, NO_TEXT: 0.2},
                "confidence": 0.6,
            },
            "goal_done": {"type": "noul", "noul": 0.03},
            "risky": {"type": "noul", "noul": 0.01},
        },
        "usage": {"input_tokens": 612, "output_tokens": 55, "cost": 0.0000257},
    }
    body.update(overrides)
    return body


class Recorder:
    """An httpx2 transport handler that records requests and replays responses."""

    def __init__(self, *responses: httpx2.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.responses.pop(0)


def _client(recorder: Recorder, **kwargs: Any) -> OpenRouterJev:
    return OpenRouterJev(
        FAKE_KEY, transport=httpx2.MockTransport(recorder), backoff_seconds=0.0, **kwargs
    )


def test_posts_the_documented_decisions_request() -> None:
    recorder = Recorder(httpx2.Response(200, json=_decisions_response()))

    with _client(recorder) as jev:
        decide(jev, 'search for "cats"', OBS, [])

    [request] = recorder.requests
    assert str(request.url) == OPENROUTER_URL == "https://openrouter.ai/api/alpha/decisions"
    assert request.method == "POST"
    assert request.headers["authorization"] == f"Bearer {FAKE_KEY}"
    assert request.headers["content-type"] == "application/json"
    body = cast(dict[str, Any], json.loads(request.content))
    assert body["model"] == OPENROUTER_MODEL == "typesafe/jev-1.13"
    assert body["state"] == render_state('search for "cats"', OBS, [])
    questions = cast(dict[str, dict[str, Any]], body["questions"])
    assert {k: q["type"] for k, q in questions.items()} == {
        "operation": "choice",
        "target": "choice",
        "text": "choice",
        "goal_done": "noul",
        "risky": "noul",
    }
    # Decisions requires instructions on every question; choices need criteria.
    assert all(isinstance(q["instructions"], str) and q["instructions"] for q in questions.values())
    assert questions["target"]["criteria"] == {
        "e1": 'e1: searchbox "Search" value=""',
        "e2": 'e2: button "Go"',
    }
    # A noul's criteria, if sent, must have both true and false; we send none.
    assert "criteria" not in questions["goal_done"]


def test_parses_answers_and_records_usage() -> None:
    recorder = Recorder(httpx2.Response(200, json=_decisions_response()))

    with _client(recorder) as jev:
        decision = decide(jev, 'search for "cats"', OBS, [])

    assert decision.operation.choice == "type"
    assert decision.operation.probability == 0.9
    assert decision.operation.confidence == 0.87
    assert decision.target is not None and decision.target.choice == "e1"
    assert decision.text is not None and decision.text.probabilities[NO_TEXT] == 0.2
    assert decision.goal_done == 0.03
    assert decision.risky == 0.01
    [call] = jev.usage
    assert call.input_tokens == 612
    assert call.output_tokens == 55
    assert call.cost == 0.0000257
    assert call.model == "typesafe/jev-1.13-20260917"
    assert call.id == "gen-dec-1-abc"
    assert call.latency_seconds >= 0.0


def test_missing_confidence_or_probabilities_never_pass_the_gate() -> None:
    """The Decisions schema only requires type + choice on a choice answer. If the rest is
    missing we must not invent certainty: confidence 0 and no probability mass."""
    body = _decisions_response()
    body["answers"]["operation"] = {"type": "choice", "choice": "click"}
    recorder = Recorder(httpx2.Response(200, json=body))

    with _client(recorder) as jev:
        decision = decide(jev, 'search for "cats"', OBS, [])

    assert decision.operation.choice == "click"
    assert decision.operation.confidence == 0.0
    assert decision.operation.probability == 0.0


def test_retries_transient_errors_then_succeeds() -> None:
    recorder = Recorder(
        httpx2.Response(429, json={"error": {"code": 429, "message": "Rate limit exceeded"}}),
        httpx2.Response(529, json={"error": {"code": 529, "message": "Provider overloaded"}}),
        httpx2.Response(200, json=_decisions_response()),
    )

    with _client(recorder) as jev:
        decision = decide(jev, 'search for "cats"', OBS, [])

    assert len(recorder.requests) == 3
    assert decision.operation.choice == "type"


def test_client_errors_raise_without_leaking_the_key() -> None:
    recorder = Recorder(
        httpx2.Response(401, json={"error": {"code": 401, "message": "No auth credentials"}})
    )

    with _client(recorder) as jev, pytest.raises(OpenRouterError) as info:
        decide(jev, 'search for "cats"', OBS, [])

    assert len(recorder.requests) == 1  # 4xx other than 429 is not retried
    assert "401" in str(info.value) and "No auth credentials" in str(info.value)
    assert FAKE_KEY not in str(info.value)
    assert FAKE_KEY not in repr(jev)


def test_gives_up_after_the_retry_budget() -> None:
    recorder = Recorder(*[httpx2.Response(503, text="unavailable") for _ in range(3)])

    with _client(recorder, max_retries=2) as jev, pytest.raises(OpenRouterError, match="503"):
        decide(jev, 'search for "cats"', OBS, [])

    assert len(recorder.requests) == 3


def test_malformed_answers_raise() -> None:
    body = _decisions_response()
    body["answers"]["goal_done"] = {"type": "noul"}
    recorder = Recorder(httpx2.Response(200, json=body))

    with _client(recorder) as jev, pytest.raises(OpenRouterError, match="goal_done"):
        jev.ask("state", build_questions('search for "cats"', OBS))


def test_empty_key_is_rejected() -> None:
    with pytest.raises(OpenRouterError, match="OPENROUTER_API_KEY"):
        OpenRouterJev("  ")


def test_usage_log_can_be_written(tmp_path: Path) -> None:
    recorder = Recorder(httpx2.Response(200, json=_decisions_response()))
    with _client(recorder) as jev:
        jev.ask("state", build_questions('search for "cats"', OBS))

    path = jev.write_usage(tmp_path / "usage.json")

    data = json.loads(path.read_text())
    assert data["calls"][0]["input_tokens"] == 612
    assert data["total"]["input_tokens"] == 612
    assert data["total"]["calls"] == 1
    assert FAKE_KEY not in path.read_text()
