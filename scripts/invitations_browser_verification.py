"""Playwright browser verification for Stage 9.3 Room Invitations & Secure Admission."""

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
    with tempfile.TemporaryDirectory(prefix="quizverse-invitations-", dir=tmp_dir, ignore_cleanup_errors=True) as temporary:
        database_path = Path(temporary) / "invitations.sqlite3"
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
                raise RuntimeError(f"The invitations browser server did not start in 15s:\n{out}")

            verify_invitations_flow(origin)
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


def verify_invitations_flow(origin: str) -> None:
    with sync_playwright() as playwright:
        browser = (
            playwright.chromium.launch(channel="msedge", headless=True)
            if EDGE_PATH.exists()
            else playwright.chromium.launch(headless=True)
        )
        try:
            _run_invitation_checks(browser, origin)
        finally:
            browser.close()


def _run_invitation_checks(browser, origin: str) -> None:
    # 1. Player Host Context
    ctx_host = browser.new_context(viewport={"width": 1280, "height": 900})
    page_host = ctx_host.new_page()
    page_host.goto(origin)

    page_host.get_by_role("button", name="Player Profile").click()
    page_host.locator("#profile-create-name").fill("HostAlice")
    with page_host.expect_response(lambda r: r.url.endswith("/api/player-profiles") and r.request.method == "POST"):
        page_host.locator('[data-action="profile-create"]').click()
    expect(page_host.locator(".profile-card h1")).to_contain_text("HostAlice")

    # 2. Player Friend Context
    ctx_friend = browser.new_context(viewport={"width": 1280, "height": 900})
    page_friend = ctx_friend.new_page()
    page_friend.goto(origin)

    page_friend.get_by_role("button", name="Player Profile").click()
    page_friend.locator("#profile-create-name").fill("FriendBob")
    with page_friend.expect_response(lambda r: r.url.endswith("/api/player-profiles") and r.request.method == "POST"):
        page_friend.locator('[data-action="profile-create"]').click()
    expect(page_friend.locator(".profile-card h1")).to_contain_text("FriendBob")

    # 3. Host and Friend become friends
    page_host.get_by_role("button", name="Social").click()
    page_host.locator('.social-nav [data-tab="search"]').click()
    page_host.locator("#player-search-input").fill("FriendBob")
    page_host.locator('[data-action="exec-player-search"]').click()
    expect(page_host.locator(".social-card-meta strong")).to_contain_text("FriendBob")
    page_host.locator('[data-action="send-friend-request"]').first.click()
    expect(page_host.locator("#toast")).to_contain_text("Friend request sent")

    page_friend.get_by_role("button", name="Social").click()
    page_friend.locator('.social-nav [data-tab="requests"]').click()
    expect(page_friend.locator(".social-card-meta strong")).to_contain_text("HostAlice")
    page_friend.locator('[data-action="accept-request"]').first.click()
    expect(page_friend.locator("#toast")).to_contain_text("Friend request accepted")

    # 4. Host creates private arena
    page_host.locator('header a.brand, header [data-view="home"]').first.click()
    page_host.locator("#game-mode").select_option("classic")
    page_host.locator("#player-name").fill("HostAlice")
    page_host.locator("details.competitive-settings summary").click()
    page_host.locator("#match-private").check()
    page_host.locator('[data-action="create"]').click()
    expect(page_host.locator("[data-action='start']")).to_be_visible()

    # Get room code
    room_code = page_host.locator(".room-code").first.inner_text().strip()
    assert len(room_code) == 6, f"Expected 6-char code, got {room_code}"

    # 5. Casual intruder Charlie attempts room code bypass -> Must fail!
    ctx_intruder = browser.new_context(viewport={"width": 1280, "height": 900})
    page_intruder = ctx_intruder.new_page()
    page_intruder.goto(origin)
    page_intruder.locator("#player-name").fill("CasualCharlie")
    page_intruder.locator('[data-action="join"]').click()
    page_intruder.locator("#room-code").fill(room_code)
    page_intruder.locator('[data-action="confirm-join"]').click()
    expect(page_intruder.locator("#toast")).to_contain_text("This arena is private and requires an invitation admission ticket", timeout=6000)

    # 6. Host invites FriendBob to the arena
    page_host.get_by_role("button", name="Social").click()
    page_host.locator('.social-nav [data-tab="friends"]').click()
    expect(page_host.locator('[data-action="invite-friend"]').first).to_be_visible()
    page_host.locator('[data-action="invite-friend"]').first.click()
    expect(page_host.locator("#toast")).to_contain_text("Arena invitation sent", timeout=6000)

    # 7. FriendBob receives invitation in Social -> Invitations tab
    page_friend.get_by_role("button", name="Social").click()
    page_friend.locator('.social-nav [data-tab="invitations"]').click()
    expect(page_friend.locator(".invitation-card")).to_be_visible(timeout=8000)
    expect(page_friend.locator(".invitation-meta strong")).to_contain_text("HostAlice")

    # 8. FriendBob accepts the invitation -> joins arena!
    page_friend.locator('[data-action="accept-invitation"]').first.click()
    expect(page_friend.locator("#toast")).to_contain_text("Joined private arena", timeout=8000)
    expect(page_friend.locator(".room-layout")).to_be_visible()

    # 9. Verify Host sees Bob in the arena lobby
    page_host.locator('header a.brand, header [data-view="play"]').first.click()
    expect(page_host.locator(".player-list")).to_contain_text("FriendBob", timeout=6000)
    print("Stage 9.3 Invitations Playwright verification completed successfully!")


if __name__ == "__main__":
    main()
