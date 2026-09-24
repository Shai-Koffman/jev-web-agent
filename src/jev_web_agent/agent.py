"""Gate + loop: code, not Jev, decides whether an answer is good enough to act on."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from jev_web_agent.decide import NO_TEXT, ChoiceResult, Decision
from jev_web_agent.models import Action, ActionRecord, Observation, as_operation

GateOutcome = Literal["pass", "done", "block", "ask"]


@dataclass(frozen=True)
class Thresholds:
    min_prob: float = 0.55  # probability of the chosen option, per relevant Choice
    min_conf: float = 0.35  # confidence, per relevant Choice
    done: float = 0.8  # goal_done Noul at or above this finishes the run
    risky: float = 0.3  # risky Noul at or above this blocks the step
    loop_repeats: int = 3  # the same (operation, target, url) this many times in a row -> ask


@dataclass(frozen=True)
class Verdict:
    outcome: GateOutcome
    reason: str
    action: Action | None  # what Jev's top answers propose (None when finishing)
    weak: tuple[str, ...] = ()  # relevant questions that missed a threshold


def relevant_questions(operation: str) -> tuple[str, ...]:
    """The answers an operation depends on."""
    if operation == "type":
        return ("operation", "target", "text")
    if operation == "click":
        return ("operation", "target")
    return ("operation",)


def passes(answer: ChoiceResult | None, th: Thresholds) -> bool:
    return (
        answer is not None
        and answer.probability >= th.min_prob
        and answer.confidence >= th.min_conf
    )


def answer_for(decision: Decision, question: str) -> ChoiceResult | None:
    return {"operation": decision.operation, "target": decision.target, "text": decision.text}[
        question
    ]


def proposed_action(decision: Decision) -> Action:
    operation = as_operation(decision.operation.choice)
    needed = relevant_questions(operation)
    target = decision.target.choice if decision.target and "target" in needed else None
    text = decision.text.choice if decision.text and "text" in needed else None
    return Action(operation, target_id=target, text=None if text == NO_TEXT else text)


def action_key(action: Action, obs: Observation) -> tuple[str, str | None, str]:
    element = obs.element(action.target_id) if action.target_id else None
    return (action.operation, element.line() if element else None, obs.url)


def record_key(record: ActionRecord) -> tuple[str, str | None, str]:
    return (record.operation, record.target_line, record.url)


def gate(
    decision: Decision, history: Sequence[ActionRecord], obs: Observation, th: Thresholds
) -> Verdict:
    """Decide what to do with Jev's answers. Order matters: done, risky, confidence, loop."""
    if decision.goal_done >= th.done:
        return Verdict("done", f"goal_done={decision.goal_done:.2f} >= {th.done}", None)
    if decision.operation.choice == "done" and passes(decision.operation, th):
        return Verdict("done", "operation 'done' passed the gate", None)

    action = proposed_action(decision)
    if decision.risky >= th.risky:
        return Verdict("block", f"risky={decision.risky:.2f} >= {th.risky}", action)

    weak = tuple(
        q
        for q in relevant_questions(action.operation)
        if not passes(answer_for(decision, q), th) or (q == "text" and action.text is None)
    )
    if weak:
        return Verdict("ask", f"low confidence on {', '.join(weak)}", action, weak)

    repeats = th.loop_repeats - 1
    recent = [record_key(r) for r in history[-repeats:]]
    if len(recent) == repeats and all(k == action_key(action, obs) for k in recent):
        return Verdict("ask", f"loop: same action {th.loop_repeats}x in a row", action)

    return Verdict("pass", "all relevant answers passed", action)
