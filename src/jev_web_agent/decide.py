"""Decide: ask Jev every question the next step might need, in ONE call (speculative fan-out).

Jev never produces an action. It answers five typed questions about the text state:

* ``operation`` (Choice) - which browser operation to take next
* ``target``    (Choice) - which tagged element to act on (options = element ids)
* ``text``      (Choice) - which of the goal's double-quoted literals to type, or ``(none)``
* ``goal_done`` (Noul)   - is the goal already achieved on this page?
* ``risky``     (Noul)   - would the next step buy / pay / send / post / delete / log in?

The gate in ``agent.py`` decides which of these answers matter and whether they are good enough.
Talking to Jev goes through the ``JevClient`` protocol so tests can inject a fake.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from typesafe_sdk import Choice, Noul, Question, TypeSafeClient

from jev_web_agent.models import ActionRecord, Observation
from jev_web_agent.observe import render_state

MODEL = "jev-latest"
NO_TEXT = "(none)"

GOAL_DONE_STATEMENT = "The goal has been achieved on the current page."
RISKY_STATEMENT = (
    "Taking this next step would buy, pay, send a message, post, delete, "
    "submit personal data, or log in."
)

# Keys are exactly models.OPERATIONS (a test pins this).
OPERATION_CRITERIA: dict[str, str] = {
    "click": "Click one of the listed interactive elements (a link, button, tab or menu item) "
    "because following or activating it leads toward the goal.",
    "type": "Type one of the goal's quoted text values into a listed text box or search box "
    "that does not already contain it.",
    "press_enter": "Press Enter to submit the text that was just typed; the box already shows "
    "the value the goal asks for.",
    "scroll_down": "Scroll down because what the goal needs is probably further down this page.",
    "go_back": "Go back to the previous page because this page is a wrong turn.",
    "done": "Stop: the current page already shows what the goal asks for.",
}

_QUOTED = re.compile(r'"([^"]*)"|“([^”]*)”')


@dataclass(frozen=True)
class ChoiceResult:
    """A Choice answer: the chosen option, every option's probability, and confidence."""

    choice: str
    probabilities: Mapping[str, float]
    confidence: float

    @property
    def probability(self) -> float:
        return self.probabilities.get(self.choice, 0.0)

    def top(self, k: int = 3) -> list[tuple[str, float]]:
        ranked = sorted(self.probabilities.items(), key=lambda item: item[1], reverse=True)
        return ranked[:k]


@dataclass(frozen=True)
class JevAnswers:
    """Jev's answers keyed by question name. Nouls carry no confidence (see docs)."""

    choices: Mapping[str, ChoiceResult]
    nouls: Mapping[str, float]


class JevClient(Protocol):
    """The one seam to Jev. ``TypeSafeJev`` is the real one; tests use a fake."""

    def ask(self, state: str, questions: Mapping[str, Question]) -> JevAnswers: ...


@dataclass(frozen=True)
class Decision:
    """Everything Jev said about one step, plus the exact state it was shown."""

    operation: ChoiceResult
    target: ChoiceResult | None
    text: ChoiceResult | None
    goal_done: float
    risky: float
    state: str


def quoted_literals(goal: str) -> list[str]:
    """The goal's double-quoted literals, in order, de-duplicated. The agent can only ever
    type text the user wrote."""
    literals: list[str] = []
    for match in _QUOTED.finditer(goal):
        literal = (match.group(1) or match.group(2) or "").strip()
        if literal and literal not in literals:
            literals.append(literal)
    return literals


def build_questions(goal: str, obs: Observation) -> dict[str, Question]:
    questions: dict[str, Question] = {
        "operation": Choice(
            instructions="Which single browser operation should be taken next to make "
            "progress toward the GOAL?",
            criteria=OPERATION_CRITERIA,
        ),
    }
    if obs.elements:
        questions["target"] = Choice(
            instructions="If the next step clicks or types, which listed interactive element "
            "should it act on to make progress toward the GOAL?",
            criteria={e.id: e.line() for e in obs.elements},
        )
    literals = quoted_literals(goal)
    if literals:
        questions["text"] = Choice(
            instructions="If the next step types text, which of the GOAL's quoted values "
            "should be typed?",
            criteria={
                **{literal: f'Type "{literal}"' for literal in literals},
                NO_TEXT: "Nothing needs to be typed next.",
            },
        )
    questions["goal_done"] = Noul(instructions=GOAL_DONE_STATEMENT)
    questions["risky"] = Noul(instructions=RISKY_STATEMENT)
    return questions


def decide(
    jev: JevClient, goal: str, obs: Observation, history: Sequence[ActionRecord]
) -> Decision:
    """One Jev call per step: all questions at once; code decides what is relevant."""
    state = render_state(goal, obs, history)
    answers = jev.ask(state, build_questions(goal, obs))
    return Decision(
        operation=answers.choices["operation"],
        target=answers.choices.get("target"),
        text=answers.choices.get("text"),
        goal_done=answers.nouls["goal_done"],
        risky=answers.nouls["risky"],
        state=state,
    )


def check_risk(
    jev: JevClient,
    goal: str,
    obs: Observation,
    history: Sequence[ActionRecord],
    next_step: str,
) -> float:
    """A second, narrow Jev call: the ``risky`` Noul about one *specific* next step.

    The fan-out call's ``risky`` answer was about Jev's own proposal. When a human picks a
    different action, that answer says nothing about the pick, so ask again with the pick
    (operation, element line, text) spelled out in the state.
    """
    state = f"{render_state(goal, obs, history)}\nNEXT STEP (about to be executed):\n{next_step}\n"
    answers = jev.ask(state, {"risky": Noul(instructions=RISKY_STATEMENT)})
    return answers.nouls["risky"]


class TypeSafeJev:
    """``JevClient`` backed by the real ``typesafe-sdk`` synchronous client."""

    def __init__(self, client: TypeSafeClient, model: str = MODEL) -> None:
        self._client = client
        self._model = model

    def ask(self, state: str, questions: Mapping[str, Question]) -> JevAnswers:
        # The SDK's recursive JSONContent alias is partially unknown to pyright strict; the call
        # itself returns a fully typed SystemOneResponse.
        response = self._client.system_one(  # pyright: ignore[reportUnknownMemberType]
            state=state, questions=questions, model=self._model
        )
        return JevAnswers(
            choices={
                name: ChoiceResult(
                    choice=answer.choice,
                    probabilities=dict(answer.probabilities),
                    confidence=answer.confidence,
                )
                for name, answer in response.choices.items()
            },
            nouls={name: answer.noul for name, answer in response.nouls.items()},
        )
