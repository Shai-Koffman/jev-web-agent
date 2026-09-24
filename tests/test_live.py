"""Live smoke tests: one against TypeSafe, one against OpenRouter's Decisions API. Each is
skipped unless its key is set (in the environment or a .env file). Never prints a key."""

import math
import os

import pytest
from dotenv import dotenv_values, find_dotenv
from typesafe_sdk import TypeSafeClient

from jev_web_agent.decide import Decision, TypeSafeJev, decide
from jev_web_agent.models import OPERATIONS, Element, Observation
from jev_web_agent.openrouter import OpenRouterJev


def _api_key(name: str) -> str:
    """Read at import time (cwd = repo root); the autouse fixture later clears the env."""
    from_env = os.environ.get(name, "").strip()
    if from_env:
        return from_env
    return (dotenv_values(find_dotenv(usecwd=True)).get(name) or "").strip()


API_KEY = _api_key("TYPESAFE_API_KEY")
OPENROUTER_KEY = _api_key("OPENROUTER_API_KEY")

pytestmark = pytest.mark.live

OBS = Observation(
    url="https://en.wikipedia.org/wiki/Main_Page",
    title="Wikipedia, the free encyclopedia",
    text="Welcome to Wikipedia, the free encyclopedia that anyone can edit.",
    elements=(
        Element(id="e1", role="searchbox", name="Search Wikipedia", value=""),
        Element(id="e2", role="button", name="Search", value=None),
        Element(id="e3", role="link", name="Donate", value=None),
    ),
)


@pytest.mark.skipif(not API_KEY, reason="TYPESAFE_API_KEY not set")
def test_real_jev_on_typesafe_answers_one_step() -> None:
    with TypeSafeClient(api_key=API_KEY) as client:
        _check(decide(TypeSafeJev(client), 'Search for "Alan Turing"', OBS, []))


@pytest.mark.skipif(not OPENROUTER_KEY, reason="OPENROUTER_API_KEY not set")
def test_real_jev_on_openrouter_answers_one_step() -> None:
    with OpenRouterJev(OPENROUTER_KEY) as jev:
        _check(decide(jev, 'Search for "Alan Turing"', OBS, []))

    [call] = jev.usage
    assert call.input_tokens is not None and call.input_tokens > 0
    assert call.model is not None and call.model.startswith("typesafe/jev-1.13")


def _check(decision: Decision) -> None:
    assert decision.operation.choice in OPERATIONS
    assert math.isclose(sum(decision.operation.probabilities.values()), 1.0, abs_tol=0.02)
    assert 0.0 <= decision.operation.confidence <= 1.0
    assert decision.target is not None and decision.target.choice in {"e1", "e2", "e3"}
    assert decision.text is not None
    assert set(decision.text.probabilities) == {"Alan Turing", "(none)"}
    assert 0.0 <= decision.goal_done <= 1.0
    assert 0.0 <= decision.risky <= 1.0
