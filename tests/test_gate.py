"""Every gate branch, as a pure function of Jev's answers."""

from jev_web_agent.agent import Thresholds, gate
from jev_web_agent.decide import NO_TEXT, ChoiceResult, Decision
from jev_web_agent.models import OPERATIONS, Action, ActionRecord, Element, Observation

URL = "https://example.test/"
OBS = Observation(
    url=URL,
    title="Example",
    text="",
    elements=(
        Element(id="e1", role="searchbox", name="Search", value=""),
        Element(id="e2", role="link", name="Alan Turing", value=None),
    ),
)
TH = Thresholds()


def choice(options: list[str], chosen: str, prob: float, confidence: float) -> ChoiceResult:
    rest = (1 - prob) / (len(options) - 1)
    return ChoiceResult(chosen, {o: prob if o == chosen else rest for o in options}, confidence)


def decision(
    operation: str = "click",
    op_prob: float = 0.9,
    op_conf: float = 0.9,
    target: str = "e2",
    target_prob: float = 0.9,
    target_conf: float = 0.9,
    text: str = NO_TEXT,
    text_prob: float = 0.9,
    text_conf: float = 0.9,
    goal_done: float = 0.05,
    risky: float = 0.01,
) -> Decision:
    return Decision(
        operation=choice(list(OPERATIONS), operation, op_prob, op_conf),
        target=choice(["e1", "e2"], target, target_prob, target_conf),
        text=choice(["Alan Turing", NO_TEXT], text, text_prob, text_conf),
        goal_done=goal_done,
        risky=risky,
        state="",
    )


def test_confident_click_passes() -> None:
    verdict = gate(decision(), [], OBS, TH)

    assert verdict.outcome == "pass"
    assert verdict.action == Action("click", target_id="e2")


def test_confident_type_passes_with_text() -> None:
    verdict = gate(decision("type", target="e1", text="Alan Turing"), [], OBS, TH)

    assert verdict.outcome == "pass"
    assert verdict.action == Action("type", target_id="e1", text="Alan Turing")


def test_operations_without_a_target_ignore_weak_target_and_text() -> None:
    weak = decision("scroll_down", target_prob=0.3, target_conf=0.0, text_prob=0.3, text_conf=0.0)

    verdict = gate(weak, [], OBS, TH)

    assert verdict.outcome == "pass"
    assert verdict.action == Action("scroll_down")


def test_goal_done_noul_finishes() -> None:
    verdict = gate(decision(goal_done=0.8), [], OBS, TH)

    assert verdict.outcome == "done"


def test_goal_done_just_below_threshold_does_not_finish() -> None:
    verdict = gate(decision(goal_done=0.79), [], OBS, TH)

    assert verdict.outcome == "pass"


def test_done_operation_passing_the_gate_finishes() -> None:
    verdict = gate(decision("done", goal_done=0.1), [], OBS, TH)

    assert verdict.outcome == "done"


def test_done_operation_below_the_gate_asks() -> None:
    verdict = gate(decision("done", op_prob=0.5, goal_done=0.1), [], OBS, TH)

    assert verdict.outcome == "ask"
    assert verdict.weak == ("operation",)


def test_risky_blocks_even_a_confident_action() -> None:
    verdict = gate(decision(risky=0.3), [], OBS, TH)

    assert verdict.outcome == "block"
    assert verdict.action == Action("click", target_id="e2")


def test_done_wins_over_risky() -> None:
    verdict = gate(decision(goal_done=0.95, risky=0.9), [], OBS, TH)

    assert verdict.outcome == "done"


def test_low_operation_probability_asks() -> None:
    verdict = gate(decision(op_prob=0.54), [], OBS, TH)

    assert verdict.outcome == "ask"
    assert verdict.weak == ("operation",)


def test_low_operation_confidence_asks() -> None:
    verdict = gate(decision(op_conf=0.34), [], OBS, TH)

    assert verdict.outcome == "ask"
    assert verdict.weak == ("operation",)


def test_low_target_confidence_asks_for_click() -> None:
    verdict = gate(decision(target_conf=0.2), [], OBS, TH)

    assert verdict.outcome == "ask"
    assert verdict.weak == ("target",)


def test_low_text_probability_asks_for_type() -> None:
    verdict = gate(decision("type", target="e1", text="Alan Turing", text_prob=0.5), [], OBS, TH)

    assert verdict.outcome == "ask"
    assert verdict.weak == ("text",)


def test_type_with_no_text_chosen_asks() -> None:
    verdict = gate(decision("type", target="e1", text=NO_TEXT), [], OBS, TH)

    assert verdict.outcome == "ask"
    assert verdict.weak == ("text",)


def test_click_without_any_target_question_asks() -> None:
    no_target = Decision(
        operation=choice(list(OPERATIONS), "click", 0.9, 0.9),
        target=None,
        text=None,
        goal_done=0.0,
        risky=0.0,
        state="",
    )

    verdict = gate(no_target, [], OBS, TH)

    assert verdict.outcome == "ask"
    assert verdict.weak == ("target",)


def test_thresholds_are_configurable() -> None:
    strict = Thresholds(min_prob=0.95)

    assert gate(decision(op_prob=0.9), [], OBS, strict).outcome == "ask"


def _record(target: str = 'e2: link "Alan Turing"', url: str = URL) -> ActionRecord:
    return ActionRecord("click", target, None, url)


def test_third_identical_action_in_a_row_asks() -> None:
    verdict = gate(decision(), [_record(), _record()], OBS, TH)

    assert verdict.outcome == "ask"
    assert verdict.reason.startswith("loop")


def test_second_identical_action_still_passes() -> None:
    verdict = gate(decision(), [_record()], OBS, TH)

    assert verdict.outcome == "pass"


def test_repeat_on_a_different_url_or_target_is_not_a_loop() -> None:
    assert gate(decision(), [_record(url="https://other/"), _record()], OBS, TH).outcome == "pass"
    assert gate(decision(), [_record('e1: x "y"'), _record()], OBS, TH).outcome == "pass"
