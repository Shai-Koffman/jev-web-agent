"""One live smoke test against the real TypeSafe API. Skipped unless TYPESAFE_API_KEY is set
(in the environment or a .env file). Never prints the key."""

import math
import os

import pytest
from dotenv import dotenv_values, find_dotenv
from typesafe_sdk import TypeSafeClient

from jev_web_agent.decide import TypeSafeJev, decide
from jev_web_agent.models import OPERATIONS, Element, Observation


def _api_key() -> str:
    from_env = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if from_env:
        return from_env
    return (dotenv_values(find_dotenv(usecwd=True)).get("TYPESAFE_API_KEY") or "").strip()


API_KEY = _api_key()

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not API_KEY, reason="TYPESAFE_API_KEY not set; live smoke test skipped"),
]

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


def test_real_jev_answers_one_step() -> None:
    with TypeSafeClient(api_key=API_KEY) as client:
        decision = decide(TypeSafeJev(client), 'Search for "Alan Turing"', OBS, [])

    assert decision.operation.choice in OPERATIONS
    assert math.isclose(sum(decision.operation.probabilities.values()), 1.0, abs_tol=0.02)
    assert 0.0 <= decision.operation.confidence <= 1.0
    assert decision.target is not None and decision.target.choice in {"e1", "e2", "e3"}
    assert decision.text is not None
    assert set(decision.text.probabilities) == {"Alan Turing", "(none)"}
    assert 0.0 <= decision.goal_done <= 1.0
    assert 0.0 <= decision.risky <= 1.0
