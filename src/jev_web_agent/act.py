"""Act: execute one gated action with Playwright.

The target is re-queried by its ``data-jev-id`` right before acting. If it is gone (or no longer
visible) the action is NOT executed and the caller re-observes instead: the page changed under us,
so Jev's answer was about a page that no longer exists.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from jev_web_agent.models import Action

ACTION_TIMEOUT_MS = 10_000
LOAD_TIMEOUT_MS = 15_000
NAVIGATION_GRACE_MS = 3_000


@dataclass(frozen=True)
class ActResult:
    executed: bool
    note: str
    gone: bool = False


def _wait_for_page(page: Page) -> None:
    # A slow page is not fatal; the next observation shows whatever is there.
    with contextlib.suppress(PlaywrightError):
        page.wait_for_load_state("load", timeout=LOAD_TIMEOUT_MS)


def act(page: Page, action: Action) -> ActResult:
    if action.operation in ("click", "type"):
        if action.target_id is None:
            return ActResult(False, f"{action.operation} needs a target")
        locator = page.locator(f'[data-jev-id="{action.target_id}"]')
        if locator.count() == 0 or not locator.first.is_visible():
            return ActResult(False, f"{action.target_id} is gone; re-observing", gone=True)
        if action.operation == "click":
            locator.first.click(timeout=ACTION_TIMEOUT_MS)
            note = f"clicked {action.target_id}"
        else:
            if action.text is None:
                return ActResult(False, "type needs text")
            locator.first.fill(action.text, timeout=ACTION_TIMEOUT_MS)
            note = f'typed "{action.text}" into {action.target_id}'
    elif action.operation == "press_enter":
        # Enter usually submits a form; give a navigation a moment to start before settling.
        # No navigation (e.g. an in-page search) is fine too.
        with (
            contextlib.suppress(PlaywrightError),
            page.expect_navigation(timeout=NAVIGATION_GRACE_MS),
        ):
            page.keyboard.press("Enter")
        note = "pressed Enter"
    elif action.operation == "scroll_down":
        page.evaluate("window.scrollBy(0, Math.round(window.innerHeight * 0.8))")
        note = "scrolled down"
    elif action.operation == "go_back":
        page.go_back(timeout=LOAD_TIMEOUT_MS)
        note = "went back"
    else:
        return ActResult(False, "done: nothing to execute")
    _wait_for_page(page)
    return ActResult(True, note)
