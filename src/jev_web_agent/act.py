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
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from jev_web_agent.models import Action, Operation

ACTION_TIMEOUT_MS = 10_000
LOAD_TIMEOUT_MS = 15_000
NAVIGATION_GRACE_MS = 3_000
TYPED_ATTR = "data-jev-typed"  # marks the element the last `type` filled

_MARK_TYPED_JS = f"""el => {{
  document.querySelectorAll('[{TYPED_ATTR}]').forEach(e => e.removeAttribute('{TYPED_ATTR}'));
  el.setAttribute('{TYPED_ATTR}', '1');
}}"""


@dataclass(frozen=True)
class ActResult:
    executed: bool
    note: str
    gone: bool = False


def _wait_for_page(page: Page) -> None:
    # A slow page is not fatal; the next observation shows whatever is there.
    with contextlib.suppress(PlaywrightError):
        page.wait_for_load_state("load", timeout=LOAD_TIMEOUT_MS)


def act(page: Page, action: Action, *, last_operation: Operation | None = None) -> ActResult:
    """Execute ``action``. ``last_operation`` is the previous *executed* operation: after a
    ``type``, ``press_enter`` goes to the element that was typed into, not whatever has focus."""
    if action.operation in ("click", "type"):
        if action.target_id is None:
            return ActResult(False, f"{action.operation} needs a target")
        locator = page.locator(f'[data-jev-id="{action.target_id}"]').first
        # is_visible() is False both when the element was removed and when it was hidden.
        if not locator.is_visible():
            return ActResult(False, f"{action.target_id} is gone; re-observing", gone=True)
        if action.operation == "type" and action.text is None:
            return ActResult(False, "type needs text")
        try:
            if action.operation == "click":
                locator.click(timeout=ACTION_TIMEOUT_MS)
                note = f"clicked {action.target_id}"
            else:
                assert action.text is not None
                locator.fill(action.text, timeout=ACTION_TIMEOUT_MS)
                locator.evaluate(_MARK_TYPED_JS)
                note = f'typed "{action.text}" into {action.target_id}'
        except PlaywrightTimeoutError:
            # e.g. an overlay covers the target: not fatal, the next observation shows why
            return ActResult(
                False,
                f"{action.operation} on {action.target_id} timed out after "
                f"{ACTION_TIMEOUT_MS} ms; re-observing",
            )
    elif action.operation == "press_enter":
        # Enter usually submits a form; give a navigation a moment to start before settling.
        # No navigation (e.g. an in-page search) is fine too.
        with (
            contextlib.suppress(PlaywrightError),
            page.expect_navigation(timeout=NAVIGATION_GRACE_MS),
        ):
            typed = page.locator(f"[{TYPED_ATTR}]").first
            if last_operation == "type" and typed.is_visible():
                typed.press("Enter", timeout=ACTION_TIMEOUT_MS)
            else:
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
