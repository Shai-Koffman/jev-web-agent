"""Fakes for hermetic tests. No mocks: these implement the same seams production uses."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast

from typesafe_sdk import Choice, Noul, Question

from jev_web_agent.decide import ChoiceResult, JevAnswers
from jev_web_agent.models import Operation


def criteria_of(question: Choice) -> dict[str, str]:
    """A Choice's options -> descriptions, as plain strings (our questions only use strings)."""
    # The SDK's recursive JSONContent alias is partially unknown to pyright strict.
    criteria = cast(
        Mapping[str, object],
        question.criteria,  # pyright: ignore[reportUnknownMemberType]
    )
    return {k: str(v) for k, v in criteria.items()}


def instructions_of(question: Choice | Noul) -> str:
    instructions = cast(object, question.instructions)  # pyright: ignore[reportUnknownMemberType]
    return str(instructions)


def confidence_of(probabilities: Mapping[str, float]) -> float:
    """The docs' approximation of Choice confidence: (n * peak - 1) / (n - 1), clamped."""
    n = len(probabilities)
    if n < 2:
        return 1.0
    peak = max(probabilities.values())
    return max(0.0, min(1.0, (n * peak - 1) / (n - 1)))


def choice_result(options: Sequence[str], chosen: str, prob: float) -> ChoiceResult:
    """A distribution with ``prob`` on ``chosen`` and the rest spread evenly. ``chosen`` need
    not be one of ``options`` (a hallucinated answer); it still gets ``prob``."""
    options = list(options) if chosen in options else [*options, chosen]
    others = [o for o in options if o != chosen]
    rest = (1.0 - prob) / len(others) if others else 0.0
    probabilities = {o: (prob if o == chosen else rest) for o in options}
    if not others:
        probabilities[chosen] = 1.0
    return ChoiceResult(
        choice=max(probabilities, key=lambda k: probabilities[k]),
        probabilities=probabilities,
        confidence=confidence_of(probabilities),
    )


@dataclass(frozen=True)
class Thought:
    """What the scripted Jev 'thinks' at one step.

    ``target`` is a substring of the element line to pick (the fake reads the target question's
    criteria, just as the real model reads them), so scripts survive element renumbering.

    ``target_id`` / ``raw_operation`` are returned verbatim, even when they are not among the
    question's options - to script a malformed or hallucinated answer.
    """

    operation: Operation
    target: str | None = None
    text: str | None = None
    goal_done: float = 0.02
    risky: float = 0.01
    prob: float = 0.9
    target_prob: float | None = None  # overrides `prob` for the target question
    target_id: str | None = None
    raw_operation: str | None = None


@dataclass
class ScriptedJev:
    """A JevClient that replays ``thoughts`` one per call (repeating the last one)."""

    thoughts: Sequence[Thought]
    think_seconds: float = 0.0  # simulated model latency (the page may change meanwhile)
    # Answers risk re-check calls (a lone `risky` question about a specific next step): given
    # the state, return the risky noul. These calls do not consume a Thought.
    risk_of: Callable[[str], float] = field(default=lambda state: 0.01)
    risk_calls: list[str] = field(default_factory=lambda: list[str]())
    calls: list[tuple[str, Mapping[str, Question]]] = field(
        default_factory=lambda: list[tuple[str, Mapping[str, Question]]]()
    )

    def ask(self, state: str, questions: Mapping[str, Question]) -> JevAnswers:
        if set(questions) == {"risky"}:
            self.risk_calls.append(state)
            return JevAnswers(choices={}, nouls={"risky": self.risk_of(state)})
        thought = self.thoughts[min(len(self.calls), len(self.thoughts) - 1)]
        if self.think_seconds:
            time.sleep(self.think_seconds)
        self.calls.append((state, questions))
        choices: dict[str, ChoiceResult] = {}
        nouls: dict[str, float] = {}
        for name, question in questions.items():
            if isinstance(question, Choice):
                options = list(criteria_of(question))
                prob = thought.target_prob if name == "target" and thought.target_prob else None
                choices[name] = choice_result(
                    options, self._pick(name, question, thought), prob or thought.prob
                )
            elif isinstance(question, Noul):
                nouls[name] = {"goal_done": thought.goal_done, "risky": thought.risky}.get(
                    name, 0.0
                )
            else:  # pragma: no cover - decide() only builds Choice and Noul objects
                raise AssertionError(f"unexpected question {name!r}: {question!r}")
        return JevAnswers(choices=choices, nouls=nouls)

    @staticmethod
    def _pick(name: str, question: Choice, thought: Thought) -> str:
        criteria = criteria_of(question)
        options = list(criteria)
        if name == "operation":
            return thought.raw_operation or thought.operation
        if name == "target":
            if thought.target_id is not None:
                return thought.target_id
            if thought.target is None:
                return options[0]
            for option, line in criteria.items():
                if thought.target in line:
                    return option
            raise AssertionError(f"scripted target {thought.target!r} not among {options}")
        if name == "text":
            return thought.text if thought.text is not None else "(none)"
        raise AssertionError(f"unexpected choice question {name!r}")


@dataclass
class ScriptedHuman:
    """A Human that answers from a script. ``picks`` maps question name -> option (or None to
    abort); ``handle_blocked`` is the answer to a risky-step block."""

    picks: Mapping[str, str | None] = field(default_factory=lambda: dict[str, str | None]())
    handle_blocked: bool = False
    asked: list[str] = field(default_factory=lambda: list[str]())

    def pick(self, question: str, reason: str, top: Sequence[tuple[str, float, str]]) -> str | None:
        self.asked.append(question)
        return self.picks.get(question)

    def resolve_blocked(self, proposal: str) -> bool:
        self.asked.append("blocked")
        return self.handle_blocked
