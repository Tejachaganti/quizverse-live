"""Playwright browser verification for Stage 9.5 Notification Center."""

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
    with tempfile.TemporaryDirectory(prefix="quizverse-notif-", dir=tmp_dir, ignore_cleanup_errors=True) as temporary:
        database_path = Path(temporary) / "notifications.sqlite3"
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
                raise RuntimeError(f"The notifications browser server did not start in 15s:\n{out}")

            verify_notifications_flow(origin)
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


def verify_notifications_flow(origin: str) -> None:
    with sync_playwright() as playwright:
        browser = (
            playwright.chromium.launch(channel="msedge", headless=True)
            if EDGE_PATH.exists()
            else playwright.chromium.launch(headless=True)
        )
        try:
            _run_notification_checks(browser, origin)
        finally:
            browser.close()


def _run_notification_checks(browser, origin: str) -> None:
    # 1. Setup Alice
    ctx_alice = browser.new_context(viewport={"width": 1280, "height": 900})
    page_alice = ctx_alice.new_page()
    page_alice.goto(origin)
    page_alice.get_by_role("button", name="Player Profile").click()
    page_alice.locator("#profile-create-name").fill("AliceNotif")
    with page_alice.expect_response(lambda r: r.url.endswith("/api/player-profiles") and r.request.method == "POST"):
        page_alice.locator('[data-action="profile-create"]').click()
    expect(page_alice.locator(".profile-card h1")).to_contain_text("AliceNotif")

    # 2. Setup Bob
    ctx_bob = browser.new_context(viewport={"width": 1280, "height": 900})
    page_bob = ctx_bob.new_page()
    page_bob.goto(origin)
    page_bob.get_by_role("button", name="Player Profile").click()
    page_bob.locator("#profile-create-name").fill("BobNotif")
    with page_bob.expect_response(lambda r: r.url.endswith("/api/player-profiles") and r.request.method == "POST"):
        page_bob.locator('[data-action="profile-create"]').click()
    expect(page_bob.locator(".profile-card h1")).to_contain_text("BobNotif")

    # 3. Verify Bell Icon & Badge initially
    bell_bob = page_bob.locator("#notif-bell-btn")
    expect(bell_bob).to_be_visible()
    badge_bob = page_bob.locator("#notif-bell-badge")
    # Badge should be hidden initially
    expect(badge_bob).to_be_hidden()

    # 4. Open Notification Center drawer on Bob
    bell_bob.click()
    panel_bob = page_bob.locator("#notif-center-panel")
    expect(panel_bob).to_have_class(re.compile(r"\bopen\b"))
    expect(panel_bob.locator(".notif-empty")).to_be_visible()

    # Close drawer
    page_bob.locator('[data-action="close-notif-center"]').click()
    expect(panel_bob).not_to_have_class(re.compile(r"\bopen\b"))

    # 5. Alice sends friend request to Bob -> real-time notification
    page_alice.get_by_role("button", name="Social").click()
    page_alice.locator('.social-nav [data-tab="search"]').click()
    page_alice.locator("#player-search-input").fill("BobNotif")
    page_alice.locator('[data-action="exec-player-search"]').click()
    expect(page_alice.locator(".social-card-meta strong")).to_contain_text("BobNotif")
    page_alice.locator('[data-action="send-friend-request"]').first.click()
    expect(page_alice.locator("#toast")).to_contain_text("Friend request sent")

    # 6. Bob receives notification in real time
    # Bell badge becomes visible and shows 1
    expect(badge_bob).to_be_visible(timeout=8000)
    expect(badge_bob).to_have_text("1")

    # Bob opens notification drawer
    bell_bob.click()
    expect(panel_bob).to_have_class(re.compile(r"\bopen\b"))
    expect(panel_bob.locator(".notif-item")).to_have_count(1)
    expect(panel_bob.locator(".notif-title")).to_contain_text("Friend Request")

    # 7. Bob marks notification read
    mark_read_btn = panel_bob.locator('[data-action="notif-toggle-read"]').first
    mark_read_btn.click()
    # Notification marked read
    expect(panel_bob.locator(".notif-item.read")).to_have_count(1, timeout=5000)
    # Badge updates to hidden
    expect(badge_bob).to_be_hidden(timeout=5000)

    # 8. Test Notification Preferences Modal
    page_bob.locator('[data-action="open-notif-prefs"]').click()
    modal_container = page_bob.locator("#notif-modal-container")
    expect(modal_container.locator(".modal-card")).to_be_visible()

    # Toggle browser_sound off and save
    sound_toggle = modal_container.locator('input[data-pref-key="browser_sound"]')
    sound_toggle.uncheck()
    modal_container.locator('[data-action="save-notif-prefs"]').click()
    expect(page_bob.locator("#toast")).to_contain_text("preferences updated")
    expect(modal_container.locator(".modal-backdrop")).to_have_count(0)

    # Re-open preferences to verify persistent state
    page_bob.locator('[data-action="open-notif-prefs"]').click()
    expect(modal_container.locator('input[data-pref-key="browser_sound"]')).not_to_be_checked()
    modal_container.locator('[data-action="close-notif-prefs"]').first.click()

    # 9. Bob accepts Alice's friend request
    page_bob.locator('[data-action="close-notif-center"]').click()
    page_bob.get_by_role("button", name="Social").click()
    page_bob.locator('.social-nav [data-tab="requests"]').click()
    page_bob.locator('[data-action="accept-request"]').first.click()
    expect(page_bob.locator("#toast")).to_contain_text("Friend request accepted")

    # 10. Alice receives friend accepted notification
    badge_alice = page_alice.locator("#notif-bell-badge")
    expect(badge_alice).to_be_visible(timeout=8000)
    page_alice.locator("#notif-bell-btn").click()
    panel_alice = page_alice.locator("#notif-center-panel")
    expect(panel_alice.locator(".notif-item")).to_have_count(1)

    # 11. Test Mark All as Read and Clear Read
    # Create another notification by Alice messaging Bob
    page_alice.locator('[data-action="close-notif-center"]').click()
    page_alice.get_by_role("button", name="Social").click()
    page_alice.locator('[data-action="refresh-social-hub"]').click()
    page_alice.locator('.social-nav [data-tab="friends"]').click()
    expect(page_alice.locator('[data-action="open-friend-chat"]')).to_be_visible(timeout=8000)
    page_alice.locator('[data-action="open-friend-chat"]').first.click()
    expect(page_alice.locator("#dm-composer-input")).to_be_visible(timeout=6000)
    page_alice.locator("#dm-composer-input").fill("Hello Bob!")
    page_alice.locator('[data-action="send-dm"]').click()

    # Bob gets real-time direct message notification
    expect(badge_bob).to_be_visible(timeout=8000)
    bell_bob.click()
    expect(panel_bob.locator(".notif-item")).to_have_count(2)  # previous read + new unread

    # Bob clicks "Mark all as read"
    panel_bob.locator('[data-action="notif-mark-all-read"]').click()
    expect(badge_bob).to_be_hidden(timeout=5000)
    expect(panel_bob.locator(".notif-item.unread")).to_have_count(0)

    # Bob clicks "Clear read"
    panel_bob.locator('[data-action="notif-clear-read"]').click()
    expect(panel_bob.locator(".notif-item")).to_have_count(0, timeout=5000)
    expect(panel_bob.locator(".notif-empty")).to_be_visible()

    # 12. Refresh page to verify persistence
    page_bob.reload()
    expect(page_bob.locator("#notif-bell-btn")).to_be_visible()
    expect(page_bob.locator("#notif-bell-badge")).to_be_hidden()

    print("STAGE 9.5 NOTIFICATION CENTER BROWSER VERIFICATION COMPLETE!")


if __name__ == "__main__":
    main()
