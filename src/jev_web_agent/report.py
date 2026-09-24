"""Record a run and write ``run.json`` + a self-contained ``report.html`` (screenshots are
relative PNGs in the same directory)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from html import escape
from pathlib import Path

from jev_web_agent.decide import ChoiceResult, Decision


@dataclass
class StepRecord:
    number: int
    url: str
    title: str
    screenshot: str | None  # file name inside the run directory
    elements: list[str]  # the element lines sent to Jev
    decision: Decision | None  # Jev's full answers (None if the call never happened)
    gate: str  # pass | done | block | ask | gone | error
    gate_reason: str
    human: str | None  # what the human decided, if asked
    action: str | None  # the action executed, if any
    result: str  # what executing it did


@dataclass
class RunRecord:
    goal: str
    start_url: str
    started: str
    model: str
    thresholds: dict[str, float | int]
    status: str = "running"  # done | aborted | blocked | max_steps | error
    reason: str = ""
    steps: list[StepRecord] = field(default_factory=lambda: list[StepRecord]())


def new_run_dir(root: Path) -> Path:
    """``root/<YYYY-MM-DD_HH-MM-SS>`` (suffixed ``-2``, ``-3``... if taken)."""
    root.mkdir(parents=True, exist_ok=True)
    base = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    candidate, n = root / base, 1
    while candidate.exists():
        n += 1
        candidate = root / f"{base}-{n}"
    candidate.mkdir()
    return candidate


def write_report(run_dir: Path, run: RunRecord) -> Path:
    (run_dir / "run.json").write_text(json.dumps(asdict(run), indent=2, ensure_ascii=False))
    report = run_dir / "report.html"
    report.write_text(_render(run), encoding="utf-8")
    return report


# --- HTML -----------------------------------------------------------------------------------

_CSS = """
:root { --bg:#f7f7f5; --card:#fff; --ink:#1d1d1f; --muted:#6b6b70; --line:#e3e3e0;
  --bar:#4a6cf7; --pass:#1f8a4c; --done:#1f6f8a; --block:#b3261e; --ask:#b26a00; }
@media (prefers-color-scheme: dark) { :root { --bg:#141416; --card:#1d1d20; --ink:#ececf0;
  --muted:#9a9aa2; --line:#303036; --bar:#7c93ff; } }
* { box-sizing: border-box; }
body { margin:0; padding:24px 16px; background:var(--bg); color:var(--ink);
  font:14px/1.45 -apple-system, system-ui, "Segoe UI", sans-serif; }
main { max-width:1200px; margin:0 auto; }
h1 { font-size:20px; margin:0 0 4px; }
.meta { color:var(--muted); margin:0 0 20px; }
.meta b { color:var(--ink); }
.step { background:var(--card); border:1px solid var(--line); border-radius:10px;
  padding:16px; margin:0 0 16px; display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr);
  gap:16px; }
@media (max-width:800px) { .step { grid-template-columns:1fr; } }
.step img { width:100%; border:1px solid var(--line); border-radius:6px; }
.step h2 { font-size:15px; margin:0 0 6px; grid-column:1 / -1; }
.url { color:var(--muted); font-weight:normal; word-break:break-all; }
.badge { display:inline-block; padding:1px 8px; border-radius:99px; color:#fff; font-size:12px;
  font-weight:600; text-transform:uppercase; }
.pass { background:var(--pass); } .done { background:var(--done); }
.block, .error, .aborted, .blocked, .abort { background:var(--block); }
.ask, .gone, .max_steps { background:var(--ask); }
table { border-collapse:collapse; width:100%; margin:6px 0 10px; }
td { padding:2px 6px; border-bottom:1px solid var(--line); vertical-align:top; }
td.p { width:52px; text-align:right; font-variant-numeric:tabular-nums; }
td.bar { width:35%; } .bar span { display:block; height:8px; background:var(--bar);
  border-radius:4px; }
tr.chosen td { font-weight:600; }
h3 { font-size:13px; margin:10px 0 2px; }
.conf { color:var(--muted); font-weight:normal; }
pre { white-space:pre-wrap; word-break:break-word; background:var(--bg); padding:8px;
  border-radius:6px; font-size:12px; margin:4px 0; }
details { margin:6px 0; } summary { cursor:pointer; color:var(--muted); }
"""


def _fmt(x: float) -> str:
    return f"{x:.2f}"


def _choice_table(name: str, answer: ChoiceResult) -> str:
    rows = "".join(
        f'<tr class="{"chosen" if option == answer.choice else ""}">'
        f"<td>{escape(option)}</td><td class=p>{_fmt(p)}</td>"
        f'<td class=bar><span style="width:{max(0.0, min(1.0, p)) * 100:.0f}%"></span></td></tr>'
        for option, p in sorted(answer.probabilities.items(), key=lambda kv: kv[1], reverse=True)
    )
    return (
        f"<h3>{escape(name)} = {escape(answer.choice)} "
        f"<span class=conf>p={_fmt(answer.probability)} · confidence {_fmt(answer.confidence)}"
        f"</span></h3><table>{rows}</table>"
    )


def _answers(decision: Decision) -> str:
    parts = [_choice_table("operation", decision.operation)]
    if decision.target is not None:
        parts.append(_choice_table("target", decision.target))
    if decision.text is not None:
        parts.append(_choice_table("text", decision.text))
    parts.append(
        "<table>"
        f"<tr><td>goal_done (noul)</td><td class=p>{_fmt(decision.goal_done)}</td></tr>"
        f"<tr><td>risky (noul)</td><td class=p>{_fmt(decision.risky)}</td></tr>"
        "</table>"
    )
    return "".join(parts)


def _step(step: StepRecord) -> str:
    shot = (
        f'<img src="{escape(step.screenshot)}" alt="screenshot of step {step.number}">'
        if step.screenshot
        else "<p class=meta>(no screenshot)</p>"
    )
    elements = escape("\n".join(step.elements)) or "(none)"
    answers = _answers(step.decision) if step.decision else "<p class=meta>(no answers)</p>"
    state = (
        f"<details><summary>exact state sent to Jev</summary><pre>{escape(step.decision.state)}"
        "</pre></details>"
        if step.decision
        else ""
    )
    lines: list[str] = [
        f"<p><span class='badge {escape(step.gate)}'>{escape(step.gate)}</span> "
        f"{escape(step.gate_reason)}</p>"
    ]
    if step.human:
        lines.append(f"<p><b>Human:</b> {escape(step.human)}</p>")
    if step.action:
        lines.append(f"<p><b>Action:</b> {escape(step.action)}</p>")
    if step.result:
        lines.append(f"<p><b>Result:</b> {escape(step.result)}</p>")
    return (
        "<section class=step>"
        f"<h2>Step {step.number} · {escape(step.title) or '(untitled)'} "
        f"<span class=url>{escape(step.url)}</span></h2>"
        f"<div>{shot}<details open><summary>elements sent ({len(step.elements)})</summary>"
        f"<pre>{elements}</pre></details></div>"
        f"<div>{''.join(lines)}{answers}{state}</div>"
        "</section>"
    )


def _render(run: RunRecord) -> str:
    thresholds = ", ".join(f"{k}={v}" for k, v in run.thresholds.items())
    steps = "".join(_step(s) for s in run.steps)
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width, initial-scale=1">'
        f"<title>Jev run report</title><style>{_CSS}</style></head><body><main>"
        f"<h1>Goal: {escape(run.goal)}</h1>"
        f"<p class=meta><span class='badge {escape(run.status)}'>{escape(run.status)}</span> "
        f"{escape(run.reason)}<br>Start <b>{escape(run.start_url)}</b> · started "
        f"{escape(run.started)} · model {escape(run.model)} · {len(run.steps)} steps · "
        f"gate {escape(thresholds)}</p>"
        f"{steps}</main></body></html>"
    )
