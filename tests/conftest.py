"""Bounded failure diagnostics for existing live OCR and UI preview gates."""

import inspect
import itertools
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit

import httpx
import pytest


def pytest_addoption(parser):
    parser.addoption("--local-postgres", action="store_true", help="Create a disposable native PostgreSQL cluster for this test process")


def pytest_configure(config):
    if not config.getoption("--local-postgres"):
        return
    root = Path(__file__).resolve().parents[1]
    installed = shutil.which("postgres")
    bindir = Path(os.environ.get("PPAP_TEST_PG_BIN", str(Path(installed).parent) if installed else "/opt/homebrew/opt/postgresql@17/bin"))
    for name in ("postgres", "initdb", "pg_ctl", "psql"):
        if not (bindir / name).is_file():
            raise pytest.UsageError(f"--local-postgres requires {name}; set PPAP_TEST_PG_BIN")
    temporary = Path(tempfile.mkdtemp(prefix="ppap-test-pg-"))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    environment = {
        "PGHOST": "127.0.0.1",
        "PGPORT": str(port),
        "PGUSER": "ppap",
        "PGPASSWORD": "ppap_isolated_dev",
        "PPAP_PG_HOST": "127.0.0.1",
        "PPAP_PG_PORT": str(port),
        "PPAP_PG_DATABASE": "ppap",
        "PPAP_PG_BOOTSTRAP_USER": "ppap",
        "PPAP_PG_BOOTSTRAP_PASSWORD": "ppap_isolated_dev",
        "PPAP_PG_MIGRATOR_USER": "ppap_migrator",
        "PPAP_PG_MIGRATOR_PASSWORD": "ppap_isolated_dev",
        "PPAP_PG_API_USER": "ppap_api",
        "PPAP_PG_API_PASSWORD": "ppap_isolated_dev",
        "PPAP_PG_WORKER_USER": "ppap_worker",
        "PPAP_PG_WORKER_PASSWORD": "ppap_isolated_dev",
        "PPAP_LOCAL_POSTGRES": str(temporary),
        "PPAP_TEST_PG_BIN": str(bindir),
        "PATH": str(bindir) + os.pathsep + os.environ.get("PATH", ""),
    }
    previous = {key: os.environ.get(key) for key in environment}
    config._ppap_local_postgres = (temporary, bindir, previous)
    os.environ.update(environment)

    def run(command):
        result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise pytest.UsageError("Local PostgreSQL setup failed: " + (result.stderr or result.stdout)[-2500:])

    try:
        run([str(bindir / "initdb"), "-D", str(temporary / "data"), "-U", "ppap", "-A", "trust", "--no-locale", "-E", "UTF8"])
        # Every fixture client uses PGHOST=127.0.0.1; no Unix socket is needed.
        run([str(bindir / "pg_ctl"), "-D", str(temporary / "data"), "-l", str(temporary / "postgres.log"), "-o", f'-h 127.0.0.1 -p {port} -k "" -c timezone=UTC', "-w", "start"])
        run([str(bindir / "psql"), "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-f", str(root / "migrations/bootstrap.sql")])
        run(["sh", str(root / "migrations/apply.sh")])
    except BaseException:
        pytest_unconfigure(config)
        raise


def pytest_unconfigure(config):
    owned = getattr(config, "_ppap_local_postgres", None)
    if owned is None:
        return
    temporary, bindir, previous = owned
    del config._ppap_local_postgres
    try:
        subprocess.run([str(bindir / "pg_ctl"), "-D", str(temporary / "data"), "-m", "immediate", "-w", "stop"], capture_output=True, timeout=30)
    finally:
        shutil.rmtree(temporary)
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture
def ocr_transport_diagnostics(monkeypatch, caplog):
    """Observe real requests without reading bodies, URLs, headers or trace info."""
    logger = logging.getLogger("tests.ocr_transport")
    caplog.set_level(logging.INFO, logger=logger.name)
    original = httpx.AsyncClient.send
    original_close = httpx.AsyncClient.aclose
    original_read = httpx.Response.aread
    sequence = itertools.count(1)
    clients = {}
    requests = {}

    async def send(client, request, *args, **kwargs):
        number, started, phase = next(sequence), monotonic(), "dispatch"
        clients[id(client)] = number
        state = {"number": number, "last_event": "dispatch"}
        requests[id(request)] = state
        previous = request.extensions.get("trace")

        async def trace(event_name, info):
            nonlocal phase
            state["last_event"] = event_name
            # Preserve the waiting phase when cancellation emits later close events.
            if event_name.endswith(".started") and "close" not in event_name:
                phase = event_name
            if previous:
                result = previous(event_name, info)
                if inspect.isawaitable(result):
                    await result

        request.extensions["trace"] = trace
        try:
            response = await original(client, request, *args, **kwargs)
            logger.info("OCR transport complete: request=%d stage=%s elapsed_ms=%d", number, phase, round((monotonic() - started) * 1000))
            return response
        except BaseException as error:
            logger.warning(
                "OCR transport stopped: request=%d stage=%s last_event=%s elapsed_ms=%d error_type=%s", number, phase, state["last_event"], round((monotonic() - started) * 1000), type(error).__name__
            )
            raise

    async def close(client):
        number, started = clients.get(id(client), 0), monotonic()
        logger.info("OCR transport close starting: request=%d", number)
        try:
            await original_close(client)
            logger.info("OCR transport close complete: request=%d elapsed_ms=%d", number, round((monotonic() - started) * 1000))
        except BaseException as error:
            logger.warning("OCR transport close stopped: request=%d elapsed_ms=%d error_type=%s", number, round((monotonic() - started) * 1000), type(error).__name__)
            raise

    async def read(response):
        try:
            return await original_read(response)
        except BaseException as error:
            try:
                state = requests.get(id(response.request), {})
            except RuntimeError:
                state = {}
            content_type = response.headers.get("content-type", "").lower()
            kind = "event-stream" if "text/event-stream" in content_type else "json" if "json" in content_type else "other"
            length = response.headers.get("content-length", "")
            declared_bytes = int(length) if length.isdecimal() and len(length) < 20 else None
            logger.warning(
                "OCR body stopped: request=%d last_event=%s status=%d downloaded_bytes=%d declared_bytes=%s kind=%s error_type=%s",
                state.get("number", 0),
                state.get("last_event", "unknown"),
                response.status_code,
                response.num_bytes_downloaded,
                declared_bytes,
                kind,
                type(error).__name__,
            )
            raise

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    monkeypatch.setattr(httpx.AsyncClient, "aclose", close)
    monkeypatch.setattr(httpx.Response, "aread", read)


def _preview_asset_path(url, origin):
    parsed = urlsplit(url)
    return parsed.path if (parsed.scheme, parsed.netloc) == (origin.scheme, origin.netloc) and parsed.path.startswith(("/preview/", "/src/", "/node_modules/", "/@")) else None


class _PreviewAssetTimings:
    """Bounded same-origin asset progress; never capture request data or queries."""

    def __init__(self, clock=monotonic):
        self.clock = clock
        self.pending = {}
        self.slow = deque(maxlen=12)
        self.started = 0
        self.finished = 0

    def start(self, request, path):
        self.started += 1
        if len(self.pending) < 128:
            self.pending[request] = (path, self.clock())

    def finish(self, request):
        self.finished += 1
        entry = self.pending.pop(request, None)
        if entry is not None:
            path, started = entry
            elapsed = round((self.clock() - started) * 1000)
            if elapsed >= 1000:
                self.slow.append({"asset": path, "elapsed_ms": elapsed})

    def snapshot(self):
        now = self.clock()
        return {
            "started": self.started,
            "finished": self.finished,
            "pending_count": self.started - self.finished,
            "pending": [{"asset": path, "elapsed_ms": round((now - started) * 1000)} for path, started in list(self.pending.values())[:12]],
            "slow_completed": list(self.slow),
        }


@pytest.fixture
def preview_asset_timing_factory():
    return _PreviewAssetTimings


@pytest.fixture
def preview_asset_path():
    return _preview_asset_path


@pytest.fixture(autouse=True)
def preview_failure_diagnostics(request, monkeypatch):
    preview = os.environ.get("PPAP_PREVIEW_URL", "")
    original = getattr(request.module, "sync_playwright", None)
    if not preview or not str(request.path).endswith("_preview.py") or not original:
        return
    from playwright.sync_api import Browser, BrowserContext

    origin = urlsplit(preview)
    pages = deque(maxlen=3)

    def asset_path(url):
        return _preview_asset_path(url, origin)

    def observe(create):
        def new_page(*args, **kwargs):
            page = create(*args, **kwargs)
            events = deque(maxlen=12)
            first_asset_failure = {}
            timings = _PreviewAssetTimings()

            def failed_asset(event):
                if path := asset_path(event.url):
                    # Chromium failure messages can contain URLs. Keep only its
                    # error code and the already-filtered same-origin path.
                    code = re.match(r"net::ERR_[A-Z0-9_]+\b", event.failure or "")
                    failure = {"request_failed": path, "error_code": code.group() if code else "unknown"}
                    if not first_asset_failure:
                        first_asset_failure.update(failure)
                    events.append(failure)

            def failed_response(event):
                if event.status >= 400 and (path := asset_path(event.url)):
                    failure = {"status": event.status, "asset": path}
                    if not first_asset_failure:
                        first_asset_failure.update(failure)
                    events.append(failure)

            page.on(
                "pageerror",
                lambda error: events.append(
                    {
                        "error_type": getattr(error, "name", "Error"),
                        "scripts": [path for url in re.findall(r"https?://[^\s)]+", (getattr(error, "stack", "") or "").split("\n", 1)[-1]) if (path := asset_path(url))][:3],
                    }
                ),
            )
            page.on("console", lambda event: events.append({"console_error": True}) if event.type == "error" else None)
            page.on("request", lambda event: timings.start(event, path) if (path := asset_path(event.url)) else None)
            page.on("requestfinished", lambda event: timings.finish(event) if asset_path(event.url) else None)
            page.on("requestfailed", lambda event: timings.finish(event) if asset_path(event.url) else None)
            page.on("requestfailed", failed_asset)
            page.on("response", failed_response)
            pages.append((page, events, first_asset_failure, timings))
            return page

        return new_page

    @contextmanager
    def observed_playwright():
        with original() as playwright:
            try:
                yield playwright
            except BaseException:
                for page, events, first_asset_failure, timings in pages:
                    result = {"first_asset_failure": first_asset_failure, "events": list(events), "assets": timings.snapshot(), "closed": page.is_closed()}
                    if not page.is_closed():
                        try:
                            result["document"] = page.locator("body").evaluate(
                                """() => ({
                                    readyState: document.readyState,
                                    visibility: document.visibilityState,
                                    appChildren: document.getElementById('app')?.childElementCount,
                                    bodyTextLength: document.body?.innerText.length,
                                    headings: document.querySelectorAll('h1,h2').length,
                                    alerts: document.querySelectorAll('[role=alert]').length,
                                    sampleButtonPresent: [...document.querySelectorAll('button')].some(node => node.textContent.trim() === '使用三类文件示例'),
                                    overviewPresent: Boolean(document.querySelector('[role=region][aria-label="全部文件概览"]')),
                                    bodyAriaHidden: document.body?.getAttribute('aria-hidden')
                                })""",
                                timeout=1000,
                            )
                            result["url"] = page.url
                            result["aria"] = page.locator("body").aria_snapshot(timeout=1000)[:4000]
                        except Exception as error:
                            result["snapshot_error_type"] = type(error).__name__
                    print("Preview failure diagnostics: " + json.dumps(result, ensure_ascii=False), file=sys.stderr)
                raise

    monkeypatch.setattr(Browser, "new_page", observe(Browser.new_page))
    monkeypatch.setattr(BrowserContext, "new_page", observe(BrowserContext.new_page))
    monkeypatch.setattr(request.module, "sync_playwright", observed_playwright)
