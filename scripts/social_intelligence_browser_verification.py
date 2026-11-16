"""Playwright browser verification for Stage 9.5 Social Intelligence & Overview."""

from __future__ import annotations

import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EDGE_PATH = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


def main() -> None:
    tmp_dir = PROJECT_ROOT / "tmp"
    tmp_dir.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="quizverse-intel-", dir=tmp_dir, ignore_cleanup_errors=True) as temporary:
        database_path = Path(temporary) / "social_intel.sqlite3"
        database_url = f"sqlite:///{database_path.as_posix()}"
        environment = {
            **os.environ,
            "DATABASE_URL": database_url,
            "PRESENCE_GRACE_PERIOD": "2.0",
        }

        migration = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        if migration.returncode:
            raise RuntimeError(f"Migration failed: {migration.stderr[-2000:]}")

        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        environment["ALLOWED_ORIGINS"] = origin

        server_log = (Path(temporary) / "uvicorn.log").open("w+", encoding="utf-8")
        server_runner = (
            "import signal; from uvicorn import Config, Server; "
            f"server = Server(Config('quizverse.app:app', host='127.0.0.1', port={port})); "
            "signal.signal(signal.SIGBREAK, lambda *_: setattr(server, 'should_exit', True)); "
            "server.run()"
        )
        process = subprocess.Popen(
            [sys.executable, "-u", "-c", server_runner],
            cwd=PROJECT_ROOT,
            env=environment,
            stdout=server_log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    server_log.flush()
                    server_log.seek(0)
                    out = server_log.read()
                    raise RuntimeError(f"Uvicorn exited early with code {process.returncode}:\n{out}")
                try:
                    with urllib.request.urlopen(f"{origin}/health", timeout=0.5) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.05)
            else:
                server_log.flush()
                server_log.seek(0)
                out = server_log.read()
                raise RuntimeError(f"The social intel browser server did not start in 15s:\n{out}")

            verify_social_intelligence_flow(origin)
        except Exception:
            server_log.flush()
            server_log.seek(0)
            print("Temporary server log:\n" + server_log.read()[-8000:], file=sys.stderr)
            raise
        finally:
            if process.poll() is None:
                process.send_signal(signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            for _attempt in range(20):
                try:
                    database_path.unlink(missing_ok=True)
                    break
                except PermissionError:
                    time.sleep(0.1)
            server_log.close()


def verify_social_intelligence_flow(origin: str) -> None:
    with sync_playwright() as playwright:
        browser = (
            playwright.chromium.launch(channel="msedge", headless=True)
            if EDGE_PATH.exists()
            else playwright.chromium.launch(headless=True)
        )
        try:
            _run_social_intelligence_checks(browser, origin)
        finally:
            browser.close()


def _run_social_intelligence_checks(browser, origin: str) -> None:
    # 1. Setup Alice
    ctx_alice = browser.new_context(viewport={"width": 1280, "height": 900})
    page_alice = ctx_alice.new_page()
    page_alice.goto(origin)
    page_alice.get_by_role("button", name="Player Profile").click()
    page_alice.locator("#profile-create-name").fill("AliceIntel")
    with page_alice.expect_response(lambda r: r.url.endswith("/api/player-profiles") and r.request.method == "POST"):
        page_alice.locator('[data-action="profile-create"]').click()
    expect(page_alice.locator(".profile-card h1")).to_contain_text("AliceIntel")

    # 2. Setup Bob
    ctx_bob = browser.new_context(viewport={"width": 1280, "height": 900})
    page_bob = ctx_bob.new_page()
    page_bob.goto(origin)
    page_bob.get_by_role("button", name="Player Profile").click()
    page_bob.locator("#profile-create-name").fill("BobIntel")
    with page_bob.expect_response(lambda r: r.url.endswith("/api/player-profiles") and r.request.method == "POST"):
        page_bob.locator('[data-action="profile-create"]').click()
    expect(page_bob.locator(".profile-card h1")).to_contain_text("BobIntel")

    # 3. Check Social Hub default tab on Alice
    page_alice.get_by_role("button", name="Social").click()
    overview_tab_btn = page_alice.locator('.social-nav [data-tab="overview"]')
    expect(overview_tab_btn).to_have_class(re.compile(r"\bactive\b"))

    # Verify overview stats grid
    expect(page_alice.locator(".overview-stats-grid")).to_be_visible()
    expect(page_alice.locator(".overview-card-val").first).to_contain_text("0")

    # 4. Quick Actions in Overview
    # Click "Find Friends" action card
    page_alice.get_by_role("button", name="🔍 Find Players").click()
    expect(page_alice.locator('.social-nav [data-tab="search"]')).to_have_class(re.compile(r"\bactive\b"))

    # Alice searches Bob and sends friend request
    page_alice.locator("#player-search-input").fill("BobIntel")
    page_alice.locator('[data-action="exec-player-search"]').click()
    expect(page_alice.locator(".social-card-meta strong")).to_contain_text("BobIntel")
    page_alice.locator('[data-action="send-friend-request"]').first.click()
    expect(page_alice.locator("#toast")).to_contain_text("Friend request sent")

    # 5. Check Bob's Overview Tab shows pending request
    page_bob.get_by_role("button", name="Social").click()
    expect(page_bob.locator('.social-nav [data-tab="overview"]')).to_have_class(re.compile(r"\bactive\b"))
    # Click pending requests card to navigate
    page_bob.locator('.overview-card[data-tab="requests"]').click()
    expect(page_bob.locator('.social-nav [data-tab="requests"]')).to_have_class(re.compile(r"\bactive\b"))
    expect(page_bob.locator(".social-card-meta strong")).to_contain_text("AliceIntel")

    # Bob accepts request
    page_bob.locator('[data-action="accept-request"]').first.click()
    expect(page_bob.locator("#toast")).to_contain_text("Friend request accepted")

    # 6. Check Overview on both: Friend count updated and recent activity shows friendship!
    page_bob.locator('.social-nav [data-tab="overview"]').click()
    expect(page_bob.locator(".overview-activity-card")).to_be_visible(timeout=8000)
    expect(page_bob.locator(".overview-activity-list")).to_contain_text("connected as friends", timeout=8000)

    page_alice.locator('.social-nav [data-tab="overview"]').click()
    expect(page_alice.locator(".overview-activity-list")).to_contain_text("connected as friends", timeout=8000)

    # Verify no leaked credentials or tokens in activity
    activity_text = page_alice.locator(".overview-activity-list").inner_text()
    assert "token" not in activity_text.lower()
    assert "password" not in activity_text.lower()
    assert "credential" not in activity_text.lower()

    print("STAGE 9.5 SOCIAL INTELLIGENCE BROWSER VERIFICATION COMPLETE!")


if __name__ == "__main__":
    main()
