"""Shared fixtures: a tiny local HTTP server for tests/fixtures and a headless page."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Page, sync_playwright

FIXTURES = Path(__file__).parent / "fixtures"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass


@pytest.fixture(scope="session")
def site() -> Iterator[str]:
    """Base URL of a local HTTP server serving tests/fixtures."""
    handler = partial(_QuietHandler, directory=str(FIXTURES))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def page() -> Iterator[Page]:
    """A fresh headless Chromium page (1280x800). Function-scoped so the CLI tests can start
    their own Playwright instance without colliding with a long-lived one."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        try:
            yield context.new_page()
        finally:
            context.close()
            browser.close()
