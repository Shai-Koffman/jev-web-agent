"""Plain data passed between the loop's stages. No behaviour beyond formatting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

Operation = Literal["click", "type", "press_enter", "scroll_down", "go_back", "done"]
OPERATIONS: tuple[Operation, ...] = get_args(Operation)


@dataclass(frozen=True)
class Element:
    """One interactive element, tagged in the page with ``data-jev-id=<id>``."""

    id: str
    role: str
    name: str
    value: str | None  # set for text inputs and selects, None otherwise

    def line(self) -> str:
        line = f'{self.id}: {self.role} "{self.name}"'
        if self.value is not None:
            line += f' value="{self.value}"'
        return line


@dataclass(frozen=True)
class Observation:
    """What the agent knows about the page at one moment."""

    url: str
    title: str
    text: str
    elements: tuple[Element, ...]

    def element(self, element_id: str) -> Element | None:
        return next((e for e in self.elements if e.id == element_id), None)


@dataclass(frozen=True)
class ActionRecord:
    """An action the agent executed, as remembered in the state sent to Jev."""

    operation: Operation
    target_line: str | None
    text: str | None
    url: str

    def describe(self) -> str:
        parts: list[str] = [self.operation]
        if self.target_line is not None:
            parts.append(f"[{self.target_line}]")
        if self.text is not None:
            parts.append(f'text="{self.text}"')
        parts.append(f"on {self.url}")
        return " ".join(parts)
