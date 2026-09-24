"""Gate + loop: code, not Jev, decides whether an answer is good enough to act on."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from playwright.sync_api import Page
from rich.console import Console
from rich.markup import escape

from jev_web_agent.act import act
from jev_web_agent.decide import (
    NO_TEXT,
    OPERATION_CRITERIA,
    ChoiceResult,
    Decision,
    JevClient,
    decide,
)
from jev_web_agent.human import Human, Option
from jev_web_agent.models import OPERATIONS, Action, ActionRecord, Observation, as_operation
from jev_web_agent.observe import observe
from jev_web_agent.report import RunRecord, StepRecord, write_report

GateOutcome = Literal["pass", "done", "block", "ask", "abort"]


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


LoopKey = tuple[str, str | None, str | None, str]  # (operation, target line, text, url)


def action_key(action: Action, obs: Observation) -> LoopKey:
    element = obs.element(action.target_id) if action.target_id else None
    return (action.operation, element.line() if element else None, action.text, obs.url)


def record_key(record: ActionRecord) -> LoopKey:
    return (record.operation, record.target_line, record.text, record.url)


def gate(
    decision: Decision, history: Sequence[ActionRecord], obs: Observation, th: Thresholds
) -> Verdict:
    """Decide what to do with Jev's answers. Order matters: done, risky, confidence, loop."""
    if decision.goal_done >= th.done:
        return Verdict("done", f"goal_done={decision.goal_done:.2f} >= {th.done}", None)
    if decision.operation.choice == "done" and passes(decision.operation, th):
        return Verdict("done", "operation 'done' passed the gate", None)

    if decision.operation.choice not in OPERATIONS:
        return Verdict(
            "abort", f"Jev returned an unknown operation {decision.operation.choice!r}", None
        )

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


# --- the loop -------------------------------------------------------------------------------


def describe(action: Action, obs: Observation) -> str:
    parts = [action.operation]
    element = obs.element(action.target_id) if action.target_id else None
    if action.target_id:
        parts.append(f"[{element.line() if element else action.target_id}]")
    if action.text is not None:
        parts.append(f'text="{action.text}"')
    return " ".join(parts)


def step_line(number: int, obs: Observation, decision: Decision, verdict: Verdict) -> str:
    """One rich-markup terminal line per step."""
    op = decision.operation
    top = escape("  ".join(f"{o} {p:.2f}" for o, p in op.top(3)))
    target = ""
    if decision.target is not None and op.choice in ("click", "type"):
        element = obs.element(decision.target.choice)
        line = element.line() if element else decision.target.choice
        target = (
            f" │ {escape(line)} (p {decision.target.probability:.2f}, "
            f"conf {decision.target.confidence:.2f})"
        )
    colour = {"pass": "green", "done": "cyan", "block": "red", "ask": "yellow", "abort": "red"}[
        verdict.outcome
    ]
    return (
        f"[bold]step {number}[/bold] │ [bold]{escape(op.choice)}[/bold]{target} │ {top} │ "
        f"conf {op.confidence:.2f} │ done {decision.goal_done:.2f} risky {decision.risky:.2f} │ "
        f"[{colour}]{verdict.outcome.upper()}[/{colour}] {escape(verdict.reason)}"
    )


class Agent:
    """Observe -> decide (one Jev call) -> gate -> act, until done / blocked / aborted."""

    def __init__(
        self,
        page: Page,
        jev: JevClient,
        human: Human,
        *,
        goal: str,
        start_url: str,
        run_dir: Path,
        console: Console,
        thresholds: Thresholds | None = None,
        max_steps: int = 15,
        model: str = "jev-latest",
    ) -> None:
        self.page = page
        self.jev = jev
        self.human = human
        self.goal = goal
        self.start_url = start_url
        self.run_dir = run_dir
        self.console = console
        self.thresholds = thresholds or Thresholds()
        self.max_steps = max_steps
        self.model = model

    def run(self) -> RunRecord:
        record = RunRecord(
            goal=self.goal,
            start_url=self.start_url,
            started=datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
            model=self.model,
            thresholds=asdict(self.thresholds),
        )
        history: list[ActionRecord] = []
        try:
            self.page.goto(self.start_url, wait_until="load")
            for number in range(1, self.max_steps + 1):
                if self._step(number, record, history):
                    break
            else:
                record.status = "max_steps"
                record.reason = f"stopped after --max-steps {self.max_steps}"
        except Exception as exc:  # report whatever happened, then let the caller decide
            record.status = "error"
            record.reason = f"{type(exc).__name__}: {exc}"
            self.console.print(f"[bold red]error:[/bold red] {escape(record.reason)}")
        finally:
            write_report(self.run_dir, record)
        return record

    def _step(self, number: int, record: RunRecord, history: list[ActionRecord]) -> bool:
        """Run one step. Returns True when the run is finished."""
        obs = observe(self.page)
        shot = f"step-{number:02d}.png"
        self.page.screenshot(path=str(self.run_dir / shot))
        step = StepRecord(
            number=number,
            url=obs.url,
            title=obs.title,
            screenshot=shot,
            elements=[e.line() for e in obs.elements],
            decision=None,
            gate="error",
            gate_reason="Jev call did not complete",
            human=None,
            action=None,
            result="",
        )
        record.steps.append(step)

        decision = decide(self.jev, self.goal, obs, history)
        verdict = gate(decision, history, obs, self.thresholds)
        step.decision, step.gate, step.gate_reason = decision, verdict.outcome, verdict.reason
        self.console.print(step_line(number, obs, decision, verdict))

        if verdict.outcome == "done":
            return self._finish(record, "done", verdict.reason)
        if verdict.outcome == "abort":
            return self._finish(record, "aborted", verdict.reason)

        action = verdict.action
        assert action is not None
        if verdict.outcome == "block":
            proposal = describe(action, obs)
            if self.human.resolve_blocked(proposal):
                step.human = "handled the risky step themselves; re-observing"
                return False
            step.human = "not handled"
            return self._finish(record, "blocked", f"risky step not executed: {proposal}")

        if verdict.outcome == "ask":
            picked = self._ask(decision, verdict, obs)
            if picked is None:
                step.human = "aborted"
                return self._finish(record, "aborted", f"{verdict.reason}; no human pick")
            step.human = f"picked {describe(picked, obs)}"
            if picked.operation == "done":
                return self._finish(record, "done", "the human said the goal is done")
            action = picked

        result = act(self.page, action)
        step.action, step.result = describe(action, obs), result.note
        if result.executed:
            element = obs.element(action.target_id) if action.target_id else None
            line = element.line() if element else None
            history.append(ActionRecord(action.operation, line, action.text, obs.url))
        else:
            self.console.print(f"  [yellow]{escape(result.note)}[/yellow]")
        return False

    def _finish(self, record: RunRecord, status: str, reason: str) -> bool:
        record.status, record.reason = status, reason
        return True

    def _ask(self, decision: Decision, verdict: Verdict, obs: Observation) -> Action | None:
        """Show Jev's top-3 for each relevant question that needs a human; None = abort."""
        assert verdict.action is not None
        loop = verdict.reason.startswith("loop")
        operation = verdict.action.operation
        if loop or "operation" in verdict.weak:
            picked = self.human.pick(
                "operation", verdict.reason, self._options("operation", decision, obs)
            )
            if picked is None:
                return None
            operation = as_operation(picked)
        chosen: dict[str, str | None] = {"target": None, "text": None}
        for question in relevant_questions(operation)[1:]:
            answer = answer_for(decision, question)
            ok = passes(answer, self.thresholds) and not (
                question == "text" and answer is not None and answer.choice == NO_TEXT
            )
            if answer is not None and ok and not loop:
                chosen[question] = answer.choice
                continue
            options = self._options(question, decision, obs)
            if not options:
                return None  # nothing to pick from (no elements / no quoted literals)
            picked = self.human.pick(question, verdict.reason, options)
            if picked is None:
                return None
            chosen[question] = picked
        return Action(operation, target_id=chosen["target"], text=chosen["text"])

    @staticmethod
    def _options(question: str, decision: Decision, obs: Observation) -> list[Option]:
        answer = answer_for(decision, question)
        if answer is None:
            return []
        ranked = [
            (o, p)
            for o, p in answer.top(len(answer.probabilities))
            if not (question == "text" and o == NO_TEXT)
        ][:3]
        options: list[Option] = []
        for option, probability in ranked:
            if question == "operation":
                meaning = OPERATION_CRITERIA.get(option, "")
            elif question == "target":
                element = obs.element(option)
                meaning = element.line() if element else ""
            else:
                meaning = f'type "{option}"'
            options.append((option, probability, meaning))
        return options
