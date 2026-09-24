"""The human in the loop: asked when Jev is unsure, looping, or proposing a risky step."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, TextIO

from rich.console import Console
from rich.markup import escape
from rich.prompt import Prompt
from rich.table import Table

# (option, Jev's probability, what the option means)
Option = tuple[str, float, str]


class Human(Protocol):
    def pick(self, question: str, reason: str, top: Sequence[Option]) -> str | None:
        """Pick one of ``top`` (return its option key) or abort (return None)."""
        ...

    def resolve_blocked(self, proposal: str) -> bool:
        """The agent refused a risky step. True = the human dealt with it themselves in the
        browser and the agent should re-observe; False = stop the run."""
        ...


class NoAskHuman:
    """``--no-ask``: never ask, always abort. For tests and CI."""

    def pick(self, question: str, reason: str, top: Sequence[Option]) -> str | None:
        return None

    def resolve_blocked(self, proposal: str) -> bool:
        return False


class TerminalHuman:
    """Asks in the terminal with rich prompts."""

    def __init__(self, console: Console, stream: TextIO | None = None) -> None:
        self._console = console
        self._stream = stream  # tests feed answers through this

    def pick(self, question: str, reason: str, top: Sequence[Option]) -> str | None:
        table = Table(title=f"Jev's top {len(top)} for [b]{question}[/b] ({escape(reason)})")
        table.add_column("#", justify="right")
        table.add_column("p", justify="right")
        table.add_column("option")
        for i, (option, probability, meaning) in enumerate(top, start=1):
            table.add_row(
                str(i), f"{probability:.2f}", f"{escape(option)}  [dim]{escape(meaning)}[/dim]"
            )
        self._console.print(table)
        choices = [str(i) for i in range(1, len(top) + 1)] + ["a"]
        answer = Prompt.ask(
            "Pick a number, or [b]a[/b] to abort",
            choices=choices,
            console=self._console,
            stream=self._stream,
        )
        return None if answer == "a" else top[int(answer) - 1][0]

    def resolve_blocked(self, proposal: str) -> bool:
        self._console.print(
            f"[bold red]Blocked a risky step:[/bold red] {escape(proposal)}\n"
            "The agent will not execute it. You can do it yourself in the browser window."
        )
        answer = Prompt.ask(
            "[b]c[/b] = I handled it, continue; [b]a[/b] = abort",
            choices=["c", "a"],
            console=self._console,
            stream=self._stream,
        )
        return answer == "c"
