"""Playwright browser verification for Stage 9.4 Direct Messaging."""

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
    with tempfile.TemporaryDirectory(prefix="quizverse-messaging-", dir=tmp_dir, ignore_cleanup_errors=True) as temporary:
        database_path = Path(temporary) / "messaging.sqlite3"
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
                raise RuntimeError(f"The messaging browser server did not start in 15s:\n{out}")

            verify_messaging_flow(origin)
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


def verify_messaging_flow(origin: str) -> None:
    with sync_playwright() as playwright:
        browser = (
            playwright.chromium.launch(channel="msedge", headless=True)
            if EDGE_PATH.exists()
            else playwright.chromium.launch(headless=True)
        )
        try:
            _run_messaging_checks(browser, origin)
        finally:
            browser.close()


def _run_messaging_checks(browser, origin: str) -> None:
    # 1. Player Alice Context
    ctx_alice = browser.new_context(viewport={"width": 1280, "height": 900})
    page_alice = ctx_alice.new_page()
    page_alice.on("console", lambda msg: print(f"ALICE CONSOLE: {msg.type}: {msg.text}"))
    page_alice.on("pageerror", lambda err: print(f"ALICE PAGEERROR: {err}"))
    page_alice.goto(origin)

    page_alice.get_by_role("button", name="Player Profile").click()
    page_alice.locator("#profile-create-name").fill("AliceMsg")
    with page_alice.expect_response(lambda r: r.url.endswith("/api/player-profiles") and r.request.method == "POST"):
        page_alice.locator('[data-action="profile-create"]').click()
    expect(page_alice.locator(".profile-card h1")).to_contain_text("AliceMsg")

    # 2. Player Bob Context
    ctx_bob = browser.new_context(viewport={"width": 1280, "height": 900})
    page_bob = ctx_bob.new_page()
    page_bob.on("console", lambda msg: print(f"BOB CONSOLE: {msg.type}: {msg.text}"))
    page_bob.on("pageerror", lambda err: print(f"BOB PAGEERROR: {err}"))
    page_bob.goto(origin)

    page_bob.get_by_role("button", name="Player Profile").click()
    page_bob.locator("#profile-create-name").fill("BobMsg")
    with page_bob.expect_response(lambda r: r.url.endswith("/api/player-profiles") and r.request.method == "POST"):
        page_bob.locator('[data-action="profile-create"]').click()
    expect(page_bob.locator(".profile-card h1")).to_contain_text("BobMsg")

    # 3. Alice and Bob become friends
    page_alice.get_by_role("button", name="Social").click()
    page_alice.locator('.social-nav [data-tab="search"]').click()
    page_alice.locator("#player-search-input").fill("BobMsg")
    page_alice.locator('[data-action="exec-player-search"]').click()
    expect(page_alice.locator(".social-card-meta strong")).to_contain_text("BobMsg")
    page_alice.locator('[data-action="send-friend-request"]').first.click()
    expect(page_alice.locator("#toast")).to_contain_text("Friend request sent")

    page_bob.get_by_role("button", name="Social").click()
    page_bob.locator('.social-nav [data-tab="requests"]').click()
    expect(page_bob.locator(".social-card-meta strong")).to_contain_text("AliceMsg")
    page_bob.locator('[data-action="accept-request"]').first.click()
    expect(page_bob.locator("#toast")).to_contain_text("Friend request accepted")

    # 4. Open Messages tab from Friends list button
    page_alice.locator('[data-action="refresh-social-hub"]').click()
    page_alice.locator('.social-nav [data-tab="friends"]').click()
    expect(page_alice.locator('[data-action="open-friend-chat"]')).to_be_visible()
    page_alice.locator('[data-action="open-friend-chat"]').first.click()

    # Verify Alice is now in the Messages tab with Bob's conversation thread active
    expect(page_alice.locator(".thread-header-meta strong")).to_contain_text("BobMsg", timeout=6000)
    expect(page_alice.locator("#dm-composer-input")).to_be_visible()

    # Bob navigates to Messages tab
    page_bob.locator('.social-nav [data-tab="messages"]').click()
    expect(page_bob.locator(".conv-list")).to_be_visible()
    expect(page_bob.locator(".conv-item")).to_be_visible(timeout=8000)
    # Bob clicks Alice's conversation item
    page_bob.locator(".conv-item").first.click()
    expect(page_bob.locator(".thread-header-meta strong")).to_contain_text("AliceMsg", timeout=6000)

    # 5. Alice types in composer -> check typing indicator on Bob
    page_alice.locator("#dm-composer-input").fill("Hello Bob!")
    # Bob should see typing indicator
    expect(page_bob.locator(".typing-indicator-bar")).to_contain_text("AliceMsg is typing", timeout=6000)

    # 6. Alice sends message
    page_alice.locator('[data-action="send-dm"]').click()
    expect(page_alice.locator(".thread-messages")).to_contain_text("Hello Bob!", timeout=6000)

    # Verify Bob receives message in real-time
    expect(page_bob.locator(".thread-messages")).to_contain_text("Hello Bob!", timeout=6000)

    # 7. Bob reacts to Alice's message with an emoji reaction
    page_bob.wait_for_timeout(300)
    page_bob.locator('.rx-btn[data-reaction="🔥"]').first.click()

    # Verify reaction pill appears on Bob and is broadcast to Alice
    expect(page_bob.locator(".rx-pill")).to_contain_text("🔥 1", timeout=6000)
    expect(page_alice.locator(".rx-pill")).to_contain_text("🔥 1", timeout=6000)

    # 8. Alice edits the message
    alice_msg = page_alice.locator(".msg-row.mine").first
    expect(alice_msg.locator('[data-action="start-edit-dm"]')).to_be_visible()
    alice_msg.locator('[data-action="start-edit-dm"]').click()
    expect(page_alice.locator(".editing-banner")).to_be_visible()
    page_alice.locator("#dm-composer-input").fill("Hello Bob, edited!")
    page_alice.locator('[data-action="submit-edit-dm"]').click()

    # Verify edited text and tag on both sides
    expect(page_alice.locator(".msg-text")).to_contain_text("Hello Bob, edited!", timeout=6000)
    expect(page_alice.locator(".msg-edited-tag")).to_be_visible()
    expect(page_bob.locator(".msg-text")).to_contain_text("Hello Bob, edited!", timeout=6000)
    expect(page_bob.locator(".msg-edited-tag")).to_be_visible()

    # 9. Alice soft-deletes the message
    alice_msg.locator('[data-action="delete-dm"]').click()
    expect(page_alice.locator("#toast")).to_contain_text("Message deleted", timeout=6000)
    expect(page_alice.locator(".msg-deleted-text")).to_be_visible()
    expect(page_bob.locator(".msg-deleted-text")).to_be_visible(timeout=6000)

    # 10. Bob sends a message, Alice reports it
    page_bob.locator("#dm-composer-input").fill("Buy cheap coins now!")
    page_bob.locator('[data-action="send-dm"]').click()
    expect(page_alice.locator(".thread-messages")).to_contain_text("Buy cheap coins now!", timeout=6000)

    bobs_msg_for_alice = page_alice.locator('.msg-row.theirs:has-text("Buy cheap coins now!")')
    expect(bobs_msg_for_alice.locator('[data-action="start-report-dm"]')).to_be_visible()
    bobs_msg_for_alice.locator('[data-action="start-report-dm"]').click()

    expect(page_alice.locator(".report-modal")).to_be_visible()
    page_alice.locator("#report-reason-select").select_option("spam")
    page_alice.locator("#report-details-input").fill("Commercial spam.")
    page_alice.locator('[data-action="submit-report-dm"]').click()
    expect(page_alice.locator("#toast")).to_contain_text("Report submitted", timeout=6000)

    # 11. Mobile Viewport Check (375x667)
    ctx_mobile = browser.new_context(viewport={"width": 375, "height": 667})
    page_mobile = ctx_mobile.new_page()
    page_mobile.goto(origin)
    page_mobile.get_by_role("button", name="Player Profile").click()
    page_mobile.locator("#profile-create-name").fill("MobileUser")
    page_mobile.locator('[data-action="profile-create"]').click()
    expect(page_mobile.locator(".profile-card h1")).to_contain_text("MobileUser")

    page_mobile.get_by_role("button", name="Social").click()
    page_mobile.locator('.social-nav [data-tab="messages"]').click()
    expect(page_mobile.locator(".messaging-shell")).to_be_visible()
    # In mobile without active chat, sidebar is visible
    expect(page_mobile.locator(".messaging-sidebar")).to_be_visible()

    print("Stage 9.4 Messaging Playwright verification completed successfully!")


if __name__ == "__main__":
    main()
