"""Edge E2E harness (Playwright, Microsoft Edge channel).

Start the throwaway stack first (see e2e/README.md), then:

    python -m pytest e2e -p no:cacheprovider            # headed, slow-mo 300 ms
    E2E_HEADLESS=1 E2E_SLOWMO=0 python -m pytest e2e     # fast, for debugging

Every test gets its own browser context with video and a Playwright trace;
a failing test also leaves a full-page screenshot:

    reports/videos/<test>/*.webm   reports/traces/<test>.zip   reports/screenshots/<test>.png
"""
import os
import re
import time

import pytest
from playwright.sync_api import sync_playwright
from pymongo import MongoClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.environ.get("E2E_REPORTS") or os.path.join(ROOT, "reports")
BASE = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8765")
MONGO = os.environ.get("E2E_MONGO", "mongodb://127.0.0.1:27099")
DB_NAME = "LeadAI_e2e"

for sub in ("videos", "screenshots", "traces"):
    os.makedirs(os.path.join(REPORTS, sub), exist_ok=True)


def pytest_configure(config):
    config.addinivalue_line("markers", "journey(name): an E2E user journey")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, "rep_" + rep.when, rep)


@pytest.fixture(scope="session")
def base_url():
    import urllib.request
    try:
        with urllib.request.urlopen(BASE + "/health", timeout=5) as r:
            assert r.status == 200
    except Exception as e:  # the stack is started outside pytest (e2e/README.md)
        pytest.exit(f"E2E server not reachable at {BASE}: {e}", returncode=2)
    return BASE


@pytest.fixture(scope="session")
def db():
    client = MongoClient(MONGO, serverSelectionTimeoutMS=3000)
    database = client[DB_NAME]
    # refuse to run against anything that is not the throwaway database
    assert database.name == "LeadAI_e2e" and "27099" in MONGO, MONGO
    yield database
    client.close()


@pytest.fixture(scope="session")
def browser():
    headless = os.environ.get("E2E_HEADLESS", "") == "1"
    slow = int(os.environ.get("E2E_SLOWMO", "300"))
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=headless, slow_mo=slow)
        yield b
        b.close()


def _slug(nodeid):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", nodeid.split("::")[-1])[:80]


class Session:
    """One browser context (= one person) with its own video and trace."""

    def __init__(self, browser, name, test_slug):
        self.name = name
        self.slug = f"{test_slug}__{name}"
        self.video_dir = os.path.join(REPORTS, "videos", test_slug)
        self.ctx = browser.new_context(viewport={"width": 1440, "height": 900}, base_url=BASE,
                                       record_video_dir=self.video_dir,
                                       record_video_size={"width": 1440, "height": 900},
                                       accept_downloads=True)
        self.ctx.tracing.start(screenshots=True, snapshots=True, sources=True, title=self.slug)
        self.page = self.ctx.new_page()
        self.console_errors = []
        self.http_errors = []
        self.page.on("console", lambda m: self.console_errors.append(m.text) if m.type == "error" else None)
        self.page.on("pageerror", lambda e: self.console_errors.append(f"pageerror: {e}"))
        self.page.on("response", self._on_response)

    def _on_response(self, r):
        if r.status >= 500:
            self.http_errors.append(f"{r.status} {r.request.method} {r.url}")

    @property
    def api(self):
        """HTTP client sharing this person's cookies (same origin as the page)."""
        return self.ctx.request

    def close(self, failed):
        if failed:
            try:
                self.page.screenshot(path=os.path.join(REPORTS, "screenshots", self.slug + ".png"), full_page=True)
            except Exception:
                pass
        self.ctx.tracing.stop(path=os.path.join(REPORTS, "traces", self.slug + ".zip"))
        video = self.page.video
        self.ctx.close()
        if video:
            try:
                os.replace(video.path(), os.path.join(self.video_dir, self.name + ".webm"))
            except OSError:
                pass


@pytest.fixture
def person(browser, base_url, request):
    """``person("customer")`` opens a fresh Edge context for that person."""
    sessions = []
    slug = _slug(request.node.nodeid)

    def make(name):
        s = Session(browser, name, slug)
        sessions.append(s)
        return s

    yield make
    failed = bool(getattr(request.node, "rep_call", None) and request.node.rep_call.failed)
    for s in sessions:
        s.close(failed)


def wait_for(fn, timeout=60, every=0.5, what="condition"):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = fn()
        if last:
            return last
        time.sleep(every)
    raise AssertionError(f"timed out after {timeout}s waiting for {what} (last={last!r})")
