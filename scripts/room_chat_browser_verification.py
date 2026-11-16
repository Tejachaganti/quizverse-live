"""Playwright browser verification for Stage 9.4 Multiplayer Room Chat."""

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
    with tempfile.TemporaryDirectory(prefix="quizverse-room-chat-", dir=tmp_dir, ignore_cleanup_errors=True) as temporary:
        database_path = Path(temporary) / "room_chat.sqlite3"
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
                raise RuntimeError(f"The room chat browser server did not start in 15s:\n{out}")

            verify_room_chat_flow(origin)
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


def verify_room_chat_flow(origin: str) -> None:
    with sync_playwright() as playwright:
        browser = (
            playwright.chromium.launch(channel="msedge", headless=True)
            if EDGE_PATH.exists()
            else playwright.chromium.launch(headless=True)
        )
        try:
            _run_room_chat_checks(browser, origin)
        finally:
            browser.close()


def _run_room_chat_checks(browser, origin: str) -> None:
    # 1. Host Alice Context
    ctx_host = browser.new_context(viewport={"width": 1280, "height": 900})
    page_host = ctx_host.new_page()
    page_host.goto(origin)

    page_host.get_by_role("button", name="Player Profile").click()
    page_host.locator("#profile-create-name").fill("AliceHost")
    with page_host.expect_response(lambda r: r.url.endswith("/api/player-profiles") and r.request.method == "POST"):
        page_host.locator('[data-action="profile-create"]').click()
    expect(page_host.locator(".profile-card h1")).to_contain_text("AliceHost")

    # 2. Player Bob Context
    ctx_bob = browser.new_context(viewport={"width": 1280, "height": 900})
    page_bob = ctx_bob.new_page()
    page_bob.goto(origin)

    page_bob.get_by_role("button", name="Player Profile").click()
    page_bob.locator("#profile-create-name").fill("BobPlayer")
    with page_bob.expect_response(lambda r: r.url.endswith("/api/player-profiles") and r.request.method == "POST"):
        page_bob.locator('[data-action="profile-create"]').click()
    expect(page_bob.locator(".profile-card h1")).to_contain_text("BobPlayer")

    # 3. Host creates public arena room
    page_host.locator('header a.brand, header [data-view="home"]').first.click()
    page_host.locator("#game-mode").select_option("classic")
    page_host.locator("#player-name").fill("AliceHost")
    page_host.locator('[data-action="create"]').click()
    expect(page_host.locator("[data-action='start']")).to_be_visible()

    room_code = page_host.locator(".room-code").first.inner_text().strip()
    assert len(room_code) == 6, f"Expected 6-char room code, got {room_code}"

    # 4. Bob joins the room
    page_bob.locator('header a.brand, header [data-view="home"]').first.click()
    page_bob.locator("#player-name").fill("BobPlayer")
    page_bob.locator('[data-action="join"]').click()
    page_bob.locator("#room-code").fill(room_code)
    page_bob.locator('[data-action="confirm-join"]').click()
    expect(page_bob.locator(".room-layout")).to_be_visible()

    # 5. Verify Room Chat toggle button is visible for both players
    expect(page_host.locator("#room-chat-toggle")).to_be_visible()
    expect(page_bob.locator("#room-chat-toggle")).to_be_visible()

    # Open chat drawer on both sides
    page_host.locator("#room-chat-toggle").click()
    expect(page_host.locator("#room-chat-drawer")).to_have_class(re.compile(r"open"))

    page_bob.locator("#room-chat-toggle").click()
    expect(page_bob.locator("#room-chat-drawer")).to_have_class(re.compile(r"open"))

    # 6. Host sends message
    page_host.locator("#room-chat-input").fill("Welcome to the quiz room!")
    page_host.locator('[data-action="send-room-chat"]').click()

    # Verify message displayed on Host and received in real-time by Bob
    expect(page_host.locator(".room-chat-messages")).to_contain_text("Welcome to the quiz room!", timeout=6000)
    expect(page_bob.locator(".room-chat-messages")).to_contain_text("Welcome to the quiz room!", timeout=6000)

    # 7. Bob reacts to message with emoji
    bob_msg = page_bob.locator(".room-chat-msg").first
    bob_msg.locator('.rx-quick-btn[data-reaction="🔥"]').click()

    # Verify reaction pill updates on both sides
    expect(page_bob.locator(".room-chat-rx-pill")).to_contain_text("🔥 1", timeout=6000)
    expect(page_host.locator(".room-chat-rx-pill")).to_contain_text("🔥 1", timeout=6000)

    # 8. Start game: ensure gameplay, question options, and timer work cleanly alongside chat drawer
    page_host.locator('[data-action="start"]').click()

    # Wait for game screen to load
    expect(page_host.locator(".quiz-card")).to_be_visible(timeout=8000)
    expect(page_bob.locator(".quiz-card")).to_be_visible(timeout=8000)

    # Verify answer buttons are visible and clickable
    expect(page_host.locator(".answer-button").first).to_be_visible()
    expect(page_bob.locator(".answer-button").first).to_be_visible()

    # Bob sends chat during live gameplay
    page_bob.locator("#room-chat-input").fill("Good luck everyone!")
    page_bob.locator('[data-action="send-room-chat"]').click()

    # Verify Host receives it in real time while on quiz card
    expect(page_host.locator(".room-chat-messages")).to_contain_text("Good luck everyone!", timeout=6000)

    # Host answers a question
    page_host.locator(".answer-button").first.click()
    expect(page_host.locator(".quiz-foot")).to_contain_text("Answer locked in", timeout=6000)

    print("Stage 9.4 Room Chat Playwright verification completed successfully!")


if __name__ == "__main__":
    main()
