"""Playwright browser verification for Stage 9.1 Social Foundation, Discovery & Friend Management."""

from __future__ import annotations

import os
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
    with tempfile.TemporaryDirectory(prefix="quizverse-social-", dir=tmp_dir) as temporary:
        database_path = Path(temporary) / "social.sqlite3"
        database_url = f"sqlite:///{database_path.as_posix()}"
        environment = {**os.environ, "DATABASE_URL": database_url}

        migration = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        if migration.returncode:
            raise RuntimeError(f"Social migration failed: {migration.stderr[-2000:]}")

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
                raise RuntimeError(f"The social browser server did not start in 15s:\n{out}")

            verify_social_flow(origin)
        except Exception:
            server_log.flush()
            server_log.seek(0)
            print("Temporary server log:\n" + server_log.read()[-8000:], file=sys.stderr)
            raise
        finally:
            if process.poll() is None:
                process.send_signal(signal.CTRL_BREAK_EVENT)
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


def verify_social_flow(origin: str) -> None:
    with sync_playwright() as playwright:
        browser = (
            playwright.chromium.launch(channel="msedge", headless=True)
            if EDGE_PATH.exists()
            else playwright.chromium.launch(headless=True)
        )
        try:
            _run_social_checks(browser, origin)
        finally:
            browser.close()


def _run_social_checks(browser, origin: str) -> None:
    # 1. Player Alpha Context
    ctx_a = browser.new_context(viewport={"width": 1280, "height": 900})
    page_a = ctx_a.new_page()
    page_a.goto(origin)

    page_a.get_by_role("button", name="Player Profile").click()
    expect(page_a.get_by_role("heading", name="Create profile")).to_be_visible()
    page_a.locator("#profile-create-name").fill("SocialAlpha")
    with page_a.expect_response(lambda r: r.url.endswith("/api/player-profiles") and r.request.method == "POST") as res_a:
        page_a.locator('[data-action="profile-create"]').click()
    data_a = res_a.value.json()
    id_a = data_a["player"]["id"]
    expect(page_a.locator(".profile-card h1")).to_contain_text("SocialAlpha")

    # 2. Player Beta Context
    ctx_b = browser.new_context(viewport={"width": 1280, "height": 900})
    page_b = ctx_b.new_page()
    page_b.goto(origin)

    page_b.get_by_role("button", name="Player Profile").click()
    expect(page_b.get_by_role("heading", name="Create profile")).to_be_visible()
    page_b.locator("#profile-create-name").fill("SocialBeta")
    with page_b.expect_response(lambda r: r.url.endswith("/api/player-profiles") and r.request.method == "POST") as res_b:
        page_b.locator('[data-action="profile-create"]').click()
    data_b = res_b.value.json()
    id_b = data_b["player"]["id"]
    expect(page_b.locator(".profile-card h1")).to_contain_text("SocialBeta")

    # 3. Player Alpha opens Social Tab & searches for Beta
    page_a.get_by_role("button", name="Social").click()
    expect(page_a.locator(".social-header h1")).to_contain_text("Social Hub")
    expect(page_a.locator(".social-empty-state")).to_contain_text("No Friends Yet")

    # Switch to Find Players tab
    page_a.locator('.social-nav [data-tab="search"]').click()
    page_a.locator("#player-search-input").fill("SocialBeta")
    page_a.locator('[data-action="exec-player-search"]').click()

    # Results should show SocialBeta
    expect(page_a.locator(".social-card-meta strong")).to_contain_text("SocialBeta")

    # 4. View Public Profile Modal
    page_a.locator('[data-action="view-public-profile"]').first.click()
    expect(page_a.locator(".social-modal")).to_be_visible()
    expect(page_a.locator(".social-modal h2")).to_contain_text("SocialBeta")
    expect(page_a.locator(".social-modal")).to_contain_text("Global Elo")

    # Close modal
    page_a.locator('[data-action="close-public-profile"]').first.click()
    expect(page_a.locator(".social-modal")).not_to_be_visible()

    # 5. Send Friend Request to SocialBeta
    page_a.locator('[data-action="send-friend-request"]').first.click()
    expect(page_a.locator("#toast")).to_contain_text("Friend request sent")

    # 6. Player Beta navigates to Social Tab -> Requests
    page_b.get_by_role("button", name="Social").click()
    # Check badge or click Requests tab
    page_b.locator('.social-nav [data-tab="requests"]').click()
    expect(page_b.locator(".social-card-meta strong")).to_contain_text("SocialAlpha")

    # Accept request
    page_b.locator('[data-action="accept-request"]').first.click()
    expect(page_b.locator("#toast")).to_contain_text("Friend request accepted")

    # Verify Beta has Alpha in Friends tab
    page_b.locator('.social-nav [data-tab="friends"]').click()
    expect(page_b.locator(".social-card-meta strong")).to_contain_text("SocialAlpha")

    # Verify Alpha has Beta in Friends tab
    page_a.locator('.social-nav [data-tab="friends"]').click()
    page_a.locator('[data-action="refresh-social-hub"]').click()
    expect(page_a.locator(".social-card-meta strong")).to_contain_text("SocialBeta")

    # 7. Remove Friend
    page_a.locator('[data-action="remove-friend"]').first.click()
    expect(page_a.locator("#toast")).to_contain_text("Friend removed")
    expect(page_a.locator(".social-empty-state")).to_contain_text("No Friends Yet")

    # 8. Re-send and test Block
    page_a.locator('.social-nav [data-tab="search"]').click()
    page_a.locator("#player-search-input").fill("SocialBeta")
    page_a.locator('[data-action="exec-player-search"]').click()
    expect(page_a.locator(".social-card-meta strong")).to_contain_text("SocialBeta")

    # Beta blocks Alpha
    page_b.locator('.social-nav [data-tab="search"]').click()
    page_b.locator("#player-search-input").fill("SocialAlpha")
    page_b.locator('[data-action="exec-player-search"]').click()
    page_b.locator('[data-action="view-public-profile"]').first.click()
    page_b.locator('.social-modal [data-action="block-player"]').click()
    expect(page_b.locator("#toast")).to_contain_text("Player blocked")

    # Alpha searches for Beta -> No results (bidirectional suppression)
    page_a.locator("#player-search-input").fill("SocialBeta")
    page_a.locator('[data-action="exec-player-search"]').click()
    expect(page_a.locator(".social-empty-state")).to_contain_text("No players found")

    # Beta unblocks Alpha
    page_b.locator('.social-nav [data-tab="blocked"]').click()
    expect(page_b.locator(".social-card-meta strong")).to_contain_text("SocialAlpha")
    page_b.locator('[data-action="unblock-player"]').first.click()
    expect(page_b.locator("#toast")).to_contain_text("Player unblocked")

    # 9. Mobile Responsive Layout Check (390x844 iPhone 12/13/14)
    ctx_mobile = browser.new_context(viewport={"width": 390, "height": 844})
    page_mobile = ctx_mobile.new_page()
    page_mobile.goto(origin)
    page_mobile.get_by_role("button", name="Social").click()

    # Check that document width does not cause horizontal overflow
    overflow = page_mobile.evaluate("() => document.documentElement.scrollWidth > window.innerWidth")
    assert not overflow, "Mobile layout has horizontal overflow in Social view!"

    ctx_mobile.close()
    ctx_a.close()
    ctx_b.close()
    print("Social browser verification completed successfully!")


if __name__ == "__main__":
    main()
