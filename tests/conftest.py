"""Shared fixtures. Tests run against the INSTALLED package (the wheel), not this source tree: ci/check.sh installs
the built wheel in a fresh venv and runs pytest from outside the repo root."""
import functools
import http.server
import threading
from pathlib import Path

import pytest

PAGES = Path(__file__).parent / "pages"


@pytest.fixture(scope="session")
def site():
    """Local web server for the test pages: deterministic, offline, no third-party sites."""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(PAGES))
    handler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(scope="session")
def loc():
    from lite_locator import Locator
    return Locator()


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser):
    p = browser.new_page(viewport={"width": 1280, "height": 900})
    yield p
    p.close()
