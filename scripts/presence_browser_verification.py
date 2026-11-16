"""Playwright browser verification for Stage 9.2 Real-Time Presence Engine, Heartbeats & Online Status."""

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
    with tempfile.TemporaryDirectory(prefix="quizverse-presence-", dir=tmp_dir, ignore_cleanup_errors=True) as temporary:
        database_path = Path(temporary) / "presence.sqlite3"
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
            raise RuntimeError(f"Presence migration failed: {migration.stderr[-2000:]}")

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
                raise RuntimeError(f"The presence browser server did not start in 15s:\n{out}")

            verify_presence_flow(origin)
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


def verify_presence_flow(origin: str) -> None:
    with sync_playwright() as playwright:
        browser = (
            playwright.chromium.launch(channel="msedge", headless=True)
            if EDGE_PATH.exists()
            else playwright.chromium.launch(headless=True)
        )
        try:
            _run_presence_checks(browser, origin)
        finally:
            browser.close()


def _run_presence_checks(browser, origin: str) -> None:
    # 1. Player Alpha Context
    ctx_a = browser.new_context(viewport={"width": 1280, "height": 900})
    page_a = ctx_a.new_page()
    page_a.goto(origin)

    page_a.get_by_role("button", name="Player Profile").click()
    page_a.locator("#profile-create-name").fill("PresAlpha")
    with page_a.expect_response(lambda r: r.url.endswith("/api/player-profiles") and r.request.method == "POST") as res_a:
        page_a.locator('[data-action="profile-create"]').click()
    data_a = res_a.value.json()
    id_a = data_a["player"]["id"]
    cred_a = data_a["credential"]
    expect(page_a.locator(".profile-card h1")).to_contain_text("PresAlpha")

    # 2. Player Beta Context
    ctx_b = browser.new_context(viewport={"width": 1280, "height": 900})
    page_b = ctx_b.new_page()
    page_b.goto(origin)

    page_b.get_by_role("button", name="Player Profile").click()
    page_b.locator("#profile-create-name").fill("PresBeta")
    with page_b.expect_response(lambda r: r.url.endswith("/api/player-profiles") and r.request.method == "POST") as res_b:
        page_b.locator('[data-action="profile-create"]').click()
    data_b = res_b.value.json()
    id_b = data_b["player"]["id"]
    cred_b = data_b["credential"]
    expect(page_b.locator(".profile-card h1")).to_contain_text("PresBeta")

    # 3. Alpha sends friend request to Beta
    page_a.get_by_role("button", name="Social").click()
    page_a.locator('.social-nav [data-tab="search"]').click()
    page_a.locator("#player-search-input").fill("PresBeta")
    page_a.locator('[data-action="exec-player-search"]').click()
    expect(page_a.locator(".social-card-meta strong")).to_contain_text("PresBeta")
    page_a.locator('[data-action="send-friend-request"]').first.click()
    expect(page_a.locator("#toast")).to_contain_text("Friend request sent")

    # 4. Beta accepts friend request
    page_b.get_by_role("button", name="Social").click()
    page_b.locator('.social-nav [data-tab="requests"]').click()
    expect(page_b.locator(".social-card-meta strong")).to_contain_text("PresAlpha")
    page_b.locator('[data-action="accept-request"]').first.click()
    expect(page_b.locator("#toast")).to_contain_text("Friend request accepted")

    # 5. Verify Online Presence on both sides
    # Beta checks Friends tab: PresAlpha is Online
    page_b.locator('.social-nav [data-tab="friends"]').click()
    expect(page_b.locator(f'[data-presence-tag="{id_a}"]')).to_have_class("social-presence-tag online", timeout=8000)
    expect(page_b.locator(f'[data-presence-label="{id_a}"]')).to_have_text("Online")

    # Alpha checks Friends tab: PresBeta is Online
    page_a.locator('.social-nav [data-tab="friends"]').click()
    page_a.locator('[data-action="refresh-social-hub"]').click()
    expect(page_a.locator(f'[data-presence-tag="{id_b}"]')).to_have_class("social-presence-tag online", timeout=8000)
    expect(page_a.locator(f'[data-presence-label="{id_b}"]')).to_have_text("Online")

    # 6. Non-friend Player Charlie connects
    ctx_c = browser.new_context(viewport={"width": 1280, "height": 900})
    page_c = ctx_c.new_page()
    page_c.goto(origin)
    page_c.get_by_role("button", name="Player Profile").click()
    page_c.locator("#profile-create-name").fill("PresCharlie")
    page_c.locator('[data-action="profile-create"]').click()
    expect(page_c.locator(".profile-card h1")).to_contain_text("PresCharlie")
    page_c.get_by_role("button", name="Social").click()
    page_c.locator('.social-nav [data-tab="friends"]').click()
    expect(page_c.locator(".social-empty-state")).to_contain_text("No Friends Yet")

    # 7. Player Alpha enters Lobby (creates room) -> Beta sees In Lobby
    page_a.locator('header a.brand, header [data-view="home"]').first.click()
    page_a.locator("#game-mode").select_option("solo_practice")
    page_a.locator("#player-name").fill("AlphaNick")
    page_a.locator('[data-action="create"]').click()
    expect(page_a.locator("[data-action='start']")).to_be_visible()

    # Beta sees PresAlpha status change to 'In Lobby'
    expect(page_b.locator(f'[data-presence-tag="{id_a}"]')).to_have_class("social-presence-tag in_lobby", timeout=8000)
    expect(page_b.locator(f'[data-presence-label="{id_a}"]')).to_have_text("In Lobby")

    # 8. Player Alpha enters Match -> Beta sees In Match
    page_a.locator('[data-action="start"]').click()
    expect(page_a.locator(".quiz-card")).to_be_visible()

    # Beta sees PresAlpha status change to 'In Match'
    expect(page_b.locator(f'[data-presence-tag="{id_a}"]')).to_have_class("social-presence-tag in_match", timeout=8000)
    expect(page_b.locator(f'[data-presence-label="{id_a}"]')).to_have_text("In Match")

    # 9. Multi-Tab Test: Alpha opens Tab 2
    page_a2 = ctx_a.new_page()
    page_a2.add_init_script(f"sessionStorage.setItem('qv-player-credential', '{cred_a}')")
    page_a2.goto(origin)
    page_a2.get_by_role("button", name="Social").click()

    # Alpha closes Tab 1 (which was in match)
    page_a.close()

    # Because Tab 2 is still connected (and Tab 2 is not in match), Alpha transitions to Online
    expect(page_b.locator(f'[data-presence-tag="{id_a}"]')).to_have_class("social-presence-tag online", timeout=8000)
    expect(page_b.locator(f'[data-presence-label="{id_a}"]')).to_have_text("Online")

    # 10. Alpha logs out on Tab 2 -> Beta sees Offline after grace period (configured to 2s)
    page_a2.locator('header [data-view="profile"]').click()
    page_a2.locator('[data-action="profile-logout"]').click()

    # Wait for grace period to expire
    expect(page_b.locator(f'[data-presence-tag="{id_a}"]')).to_have_class("social-presence-tag offline", timeout=10000)
    expect(page_b.locator(f'[data-presence-label="{id_a}"]')).to_have_text("Offline")

    # 11. Alpha reconnects / signs back in -> Beta sees Online again
    page_a2.locator("#profile-credential").fill(cred_a)
    page_a2.locator('[data-action="profile-signin"]').click()
    expect(page_a2.locator(".profile-card h1")).to_contain_text("PresAlpha")

    expect(page_b.locator(f'[data-presence-tag="{id_a}"]')).to_have_class("social-presence-tag online", timeout=8000)
    expect(page_b.locator(f'[data-presence-label="{id_a}"]')).to_have_text("Online")

    # 12. Beta blocks Alpha -> immediate suppression
    page_b.locator(f'[data-action="block-player"][data-player-id="{id_a}"]').click()
    expect(page_b.locator("#toast")).to_contain_text("Player blocked")

    # In Beta's friends tab, Alpha is gone
    expect(page_b.locator(f'[data-presence-tag="{id_a}"]')).not_to_be_visible()

    # 13. Mobile Viewport Check (390x844)
    page_m = ctx_b.new_page()
    page_m.set_viewport_size({"width": 390, "height": 844})
    page_m.goto(origin)
    page_m.get_by_role("button", name="Social").click()
    expect(page_m.locator(".social-header h1")).to_contain_text("Social Hub")
    overflow = page_m.evaluate("() => document.documentElement.scrollWidth > window.innerWidth")
    assert not overflow, "Mobile viewport must not introduce horizontal overflow"

    print("Presence browser verification completed successfully!")


if __name__ == "__main__":
    main()
