"""``jev-agent``: run the loop on a starter task or on any goal + start URL."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx2
from dotenv import find_dotenv, load_dotenv
from playwright.sync_api import sync_playwright
from rich.console import Console
from rich.markup import escape
from typesafe_sdk import TypeSafeClient

from jev_web_agent.agent import Agent, Thresholds
from jev_web_agent.decide import MODEL, JevClient, TypeSafeJev
from jev_web_agent.human import Human, NoAskHuman, TerminalHuman
from jev_web_agent.openrouter import OPENROUTER_MODEL, OpenRouterJev
from jev_web_agent.report import RunRecord, new_run_dir

API_KEY_ENV = "TYPESAFE_API_KEY"
OPENROUTER_KEY_ENV = "OPENROUTER_API_KEY"
BACKENDS = ("openrouter", "typesafe")
DEFAULT_MODELS = {"openrouter": OPENROUTER_MODEL, "typesafe": MODEL}


@dataclass(frozen=True)
class Task:
    url: str
    goal: str


# Read-only public sites.
TASKS: dict[str, Task] = {
    "wiki": Task(
        "https://en.wikipedia.org",
        'Search for "Alan Turing", open his article, and stop when the Alan Turing article '
        "is showing.",
    ),
    "hn": Task(
        "https://news.ycombinator.com",
        "Open the comments page of the top story on the front page.",
    ),
    "books": Task(
        "https://books.toscrape.com",
        'Open the book page for "A Light in the Attic".',
    ),
}


class MissingApiKeyError(RuntimeError):
    pass


def build_parser() -> argparse.ArgumentParser:
    defaults = Thresholds()
    parser = argparse.ArgumentParser(
        prog="jev-agent",
        description="Drive a browser with Jev (TypeSafe System One). A human decides when Jev "
        "is unsure.",
    )
    what = parser.add_mutually_exclusive_group(required=True)
    what.add_argument("--task", choices=sorted(TASKS), help="a starter task")
    what.add_argument("--goal", help='what to do; put text to type in double quotes: "..."')
    parser.add_argument("--url", help="start URL (required with --goal)")
    parser.add_argument("--headless", action="store_true", help="hide the browser window")
    parser.add_argument(
        "--no-ask", action="store_true", help="abort instead of asking a human (tests/CI)"
    )
    parser.add_argument("--max-steps", type=int, default=15)
    parser.add_argument("--min-prob", type=float, default=defaults.min_prob)
    parser.add_argument("--min-conf", type=float, default=defaults.min_conf)
    parser.add_argument("--done-threshold", type=float, default=defaults.done)
    parser.add_argument("--risky-threshold", type=float, default=defaults.risky)
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument(
        "--backend",
        choices=BACKENDS,
        help="where Jev runs (default: openrouter if OPENROUTER_API_KEY is set, else typesafe)",
    )
    parser.add_argument(
        "--model", help=f"default: {OPENROUTER_MODEL} on openrouter, {MODEL} on typesafe"
    )
    return parser


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.task:
        if args.url:
            parser.error("--url goes with --goal, not --task")
        args.url, args.goal = TASKS[args.task].url, TASKS[args.task].goal
    elif not args.url:
        parser.error("--goal needs --url")
    return args


def resolve_backend(requested: str | None) -> str:
    """An explicit --backend wins; otherwise openrouter when its key is set."""
    if requested:
        return requested
    return "openrouter" if os.environ.get(OPENROUTER_KEY_ENV, "").strip() else "typesafe"


def _require(env: str) -> str:
    key = os.environ.get(env, "").strip()
    if not key:
        raise MissingApiKeyError(f"{env} is not set. Put it in .env or the environment.")
    return key


def run(
    argv: Sequence[str] | None = None,
    *,
    jev: JevClient | None = None,
    human: Human | None = None,
    console: Console | None = None,
    transport: httpx2.BaseTransport | None = None,
) -> tuple[RunRecord, Path]:
    """Parse args, open the browser, run the agent, write the report.

    ``jev``/``human`` default to the real backend client and the terminal; tests inject fakes
    here and nothing else changes. ``transport`` replaces only the OpenRouter client's HTTP
    transport (for hermetic tests of the real adapter).
    """
    load_dotenv(find_dotenv(usecwd=True))  # .env where you run it, if present
    args = parse_args(argv)
    console = console or Console()
    backend = resolve_backend(args.backend)
    model: str = args.model or DEFAULT_MODELS[backend]
    with contextlib.ExitStack() as stack:
        if jev is None:
            if backend == "openrouter":
                jev = stack.enter_context(
                    OpenRouterJev(_require(OPENROUTER_KEY_ENV), model=model, transport=transport)
                )
            else:
                _require(API_KEY_ENV)
                client = stack.enter_context(TypeSafeClient(model=model))
                jev = TypeSafeJev(client, model=model)
        if human is None:
            human = NoAskHuman() if args.no_ask else TerminalHuman(console)

        run_dir = new_run_dir(args.runs_dir)
        playwright = stack.enter_context(sync_playwright())
        browser = playwright.chromium.launch(headless=args.headless)
        stack.callback(browser.close)
        page = browser.new_context(viewport={"width": 1280, "height": 800}).new_page()

        console.print(
            f"[bold]Goal:[/bold] {escape(args.goal)}\n[bold]Start:[/bold] {escape(args.url)}\n"
            f"[bold]Jev:[/bold] {backend} · {escape(model)}"
        )
        agent = Agent(
            page,
            jev,
            human,
            goal=args.goal,
            start_url=args.url,
            run_dir=run_dir,
            console=console,
            thresholds=Thresholds(
                min_prob=args.min_prob,
                min_conf=args.min_conf,
                done=args.done_threshold,
                risky=args.risky_threshold,
            ),
            max_steps=args.max_steps,
            model=model,
        )
        record = agent.run()
        if isinstance(jev, OpenRouterJev):
            usage = jev.write_usage(run_dir / "usage.json")
            total = json.loads(usage.read_text())["total"]
            console.print(
                f"openrouter: {total['calls']} calls, {total['latency_seconds'] or 0:.1f}s, "
                f"{total['input_tokens']} input tokens, {total['output_tokens']} output tokens, "
                f"cost ${total['cost'] or 0:.6f}"
            )
    console.print(
        f"[bold]{record.status.upper()}[/bold] {escape(record.reason)}\n"
        f"Report: {(run_dir / 'report.html').resolve()}"
    )
    return record, run_dir


def main(argv: Sequence[str] | None = None) -> int:
    try:
        record, _ = run(argv)
    except MissingApiKeyError as exc:
        print(f"jev-agent: {exc}", file=sys.stderr)
        return 2
    return 0 if record.status == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
