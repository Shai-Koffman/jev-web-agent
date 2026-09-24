import json
from typing import Any, cast

import httpx2
import pytest
from typesafe_sdk import Choice, Noul, RetryPolicy, TypeSafeClient

from jev_web_agent.decide import (
    NO_TEXT,
    ChoiceResult,
    TypeSafeJev,
    build_questions,
    decide,
    quoted_literals,
)
from jev_web_agent.models import OPERATIONS, ActionRecord, Element, Observation
from jev_web_agent.observe import render_state
from tests.fakes import ScriptedJev, Thought, criteria_of, instructions_of

OBS = Observation(
    url="https://example.test/",
    title="Example",
    text="Hello",
    elements=(
        Element(id="e1", role="searchbox", name="Search", value=""),
        Element(id="e2", role="button", name="Go", value=None),
    ),
)


@pytest.mark.parametrize(
    ("goal", "expected"),
    [
        ('search for "Alan Turing"', ["Alan Turing"]),
        ('type "a" then "b" then "a" again', ["a", "b"]),
        ("open “A Light in the Attic”", ["A Light in the Attic"]),
        ('an empty "" literal and "  padded  "', ["padded"]),
        ("no quotes at all", []),
    ],
)
def test_quoted_literals(goal: str, expected: list[str]) -> None:
    assert quoted_literals(goal) == expected


def test_one_call_with_all_five_questions() -> None:
    jev = ScriptedJev([Thought("click", target="Go")])
    history = [ActionRecord("type", 'e1: searchbox "Search" value=""', "cats", OBS.url)]

    decide(jev, 'search for "cats"', OBS, history)

    assert len(jev.calls) == 1
    state, questions = jev.calls[0]
    assert state == render_state('search for "cats"', OBS, history)
    assert set(questions) == {"operation", "target", "text", "goal_done", "risky"}


def test_question_shapes() -> None:
    questions = build_questions('search for "cats"', OBS)

    operation = questions["operation"]
    assert isinstance(operation, Choice)
    assert tuple(criteria_of(operation)) == OPERATIONS
    assert all(criteria_of(operation).values())

    target = questions["target"]
    assert isinstance(target, Choice)
    assert criteria_of(target) == {
        "e1": 'e1: searchbox "Search" value=""',
        "e2": 'e2: button "Go"',
    }

    text = questions["text"]
    assert isinstance(text, Choice)
    assert list(criteria_of(text)) == ["cats", NO_TEXT]

    goal_done = questions["goal_done"]
    risky = questions["risky"]
    assert isinstance(goal_done, Noul)
    assert isinstance(risky, Noul)
    assert instructions_of(goal_done) == "The goal has been achieved on the current page."
    assert instructions_of(risky) == (
        "Taking this next step would buy, pay, send a message, post, delete, "
        "submit personal data, or log in."
    )


def test_target_and_text_are_omitted_when_there_is_nothing_to_choose() -> None:
    empty = Observation(url="about:blank", title="", text="", elements=())

    questions = build_questions("no literals here", empty)

    assert set(questions) == {"operation", "goal_done", "risky"}


def test_decide_parses_answers() -> None:
    jev = ScriptedJev([Thought("type", target="Search", text="cats", goal_done=0.1, risky=0.2)])

    decision = decide(jev, 'search for "cats"', OBS, [])

    assert decision.operation.choice == "type"
    assert decision.target is not None and decision.target.choice == "e1"
    assert decision.text is not None and decision.text.choice == "cats"
    assert decision.goal_done == 0.1
    assert decision.risky == 0.2
    assert decision.state.startswith('GOAL: search for "cats"')


def test_choice_result_top_k_and_probability() -> None:
    result = ChoiceResult(
        choice="b", probabilities={"a": 0.2, "b": 0.5, "c": 0.25, "d": 0.05}, confidence=0.4
    )

    assert result.probability == 0.5
    assert result.top(3) == [("b", 0.5), ("c", 0.25), ("a", 0.2)]


def test_typesafe_adapter_round_trips_through_the_real_sdk() -> None:
    """The real SDK client, with only its HTTP transport replaced: proves our questions
    serialise to the documented wire shape and the documented answer fields parse."""
    seen: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx2.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {
                    "operation": {
                        "type": "choice",
                        "choice": "click",
                        "probabilities": dict.fromkeys(OPERATIONS, 0.0) | {"click": 1.0},
                        "confidence": 1.0,
                    },
                    "target": {
                        "type": "choice",
                        "choice": "e2",
                        "probabilities": {"e1": 0.1, "e2": 0.9},
                        "confidence": 0.8,
                    },
                    "text": {
                        "type": "choice",
                        "choice": NO_TEXT,
                        "probabilities": {"cats": 0.3, NO_TEXT: 0.7},
                        "confidence": 0.4,
                    },
                    "goal_done": {"type": "noul", "noul": 0.05},
                    "risky": {"type": "noul", "noul": 0.01},
                },
                "usage": {"input_tokens": 500, "output_tokens": 40},
            },
        )

    client = TypeSafeClient(
        api_key="test-key-not-real",
        transport=httpx2.MockTransport(handler),
        retry=RetryPolicy(max_retries=0),
    )
    with client:
        decision = decide(TypeSafeJev(client), 'search for "cats"', OBS, [])

    assert seen["url"] == "https://api.typesafe.ai/v1/systemone"
    body = cast(dict[str, Any], seen["body"])
    assert body["model"] == "jev-latest"
    assert body["state"] == render_state('search for "cats"', OBS, [])
    questions = cast(dict[str, dict[str, Any]], body["questions"])
    assert {k: q["type"] for k, q in questions.items()} == {
        "operation": "choice",
        "target": "choice",
        "text": "choice",
        "goal_done": "noul",
        "risky": "noul",
    }
    assert questions["target"]["criteria"] == {
        "e1": 'e1: searchbox "Search" value=""',
        "e2": 'e2: button "Go"',
    }
    assert decision.operation.choice == "click"
    assert decision.operation.confidence == 1.0
    assert decision.target == ChoiceResult("e2", {"e1": 0.1, "e2": 0.9}, 0.8)
    assert decision.text is not None and decision.text.choice == NO_TEXT
    assert decision.goal_done == 0.05
    assert decision.risky == 0.01
