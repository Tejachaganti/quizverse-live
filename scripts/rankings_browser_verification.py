"""Browser verification for Stage 8.2 persistent competitive Elo rankings."""

from __future__ import annotations

import os
import signal
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from quizverse.questions import QUESTIONS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EDGE_PATH = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="quizverse-rankings-") as temporary:
        database_path = Path(temporary) / "rankings.sqlite3"
        database_url = f"sqlite:///{database_path.as_posix()}"
        environment = {**os.environ, "DATABASE_URL": database_url}
        migration = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=PROJECT_ROOT, env=environment, capture_output=True, text=True, check=False,
        )
        if migration.returncode:
            raise RuntimeError(f"Rankings migration failed: {migration.stderr[-2000:]}")
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
            [sys.executable, "-c", server_runner],
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
                    raise RuntimeError(f"Uvicorn exited with code {process.returncode}:\n{out}")
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
                raise RuntimeError(f"The rankings browser server did not start in 15s:\n{out}")
            verify_rankings_flow(origin)
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


def verify_rankings_flow(origin: str) -> None:
    with sync_playwright() as playwright:
        browser = (
            playwright.chromium.launch(channel="msedge", headless=True)
            if EDGE_PATH.exists()
            else playwright.chromium.launch(headless=True)
        )
        try:
            _verify_rankings_pages(browser, origin)
        finally:
            browser.close()


def _verify_rankings_pages(browser, origin: str) -> None:
    desktop_context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = desktop_context.new_page()
    page.set_default_timeout(10000)

    # 1. Navigate to home and open Rankings
    page.goto(origin, wait_until="domcontentloaded")
    page.get_by_role("button", name="Rankings").click()

    expect(page.locator(".rankings-card h1")).to_contain_text("Arena Leaderboard")
    expect(page.locator(".section-kicker")).to_contain_text("COMPETITIVE RANKINGS")

    # Verify scope buttons exist
    scope_buttons = page.locator(".btn-scope")
    expect(scope_buttons.first).to_contain_text("Global")
    expect(page.locator('.btn-scope[data-scope="global"]')).to_have_class("btn btn-scope active")

    # Initially empty state
    expect(page.locator(".profile-empty")).to_be_visible()
    expect(page.locator(".profile-empty")).to_contain_text("No ranked players")

    # 2. Scope switching
    page.locator('.btn-scope[data-scope="classic"]').click()
    expect(page.locator('.btn-scope[data-scope="classic"]')).to_have_class("btn btn-scope active")
    expect(page.locator(".profile-empty")).to_be_visible()

    # 3. Create Player 1 Profile
    page.get_by_role("button", name="Player Profile").click()
    page.locator("#profile-create-name").fill("Ranked Ace")
    page.locator('[data-action="profile-create"]').click()
    expect(page.locator(".profile-card h1")).to_contain_text("Ranked Ace")

    # Create Player 2 in a second browser context
    context2 = browser.new_context(viewport={"width": 1280, "height": 900})
    page2 = context2.new_page()
    page2.set_default_timeout(10000)
    page2.goto(origin, wait_until="domcontentloaded")
    page2.get_by_role("button", name="Player Profile").click()
    page2.locator("#profile-create-name").fill("Ranked Rival")
    page2.locator('[data-action="profile-create"]').click()
    expect(page2.locator(".profile-card h1")).to_contain_text("Ranked Rival")

    # 4. Host a 2-player Classic match
    page.get_by_role("button", name="Play", exact=True).click()
    page.locator("#player-name").fill("Ace")
    page.locator("#game-mode").select_option("classic")
    page.locator(".competitive-settings summary").click()
    page.locator("#match-count").fill("1")
    page.locator('[data-action="create"]').click()

    # Wait for lobby room code
    expect(page.locator(".room-code")).to_be_visible()
    room_code = page.locator(".room-code").inner_text().strip()

    # Player 2 joins
    page2.get_by_role("button", name="Play", exact=True).click()
    page2.locator("#player-name").fill("Rival")
    page2.locator('[data-action="join"]').click()
    page2.locator("#room-code").fill(room_code)
    page2.locator('[data-action="confirm-join"]').click()

    # Wait for Rival to appear in Ace's lobby
    expect(page.locator(".player-row").nth(1)).to_be_visible()

    # Start game
    page.locator('[data-action="start"]').click()
    expect(page.locator(".answer-button").first).to_be_visible(timeout=12000)
    expect(page2.locator(".answer-button").first).to_be_visible(timeout=12000)

    # Ace answers correctly, Rival answers incorrectly
    prompt = page.locator(".question-text").inner_text()
    question = next(item for item in QUESTIONS if item.prompt == prompt)
    correct_idx = question.answer
    wrong_idx = (correct_idx + 1) % len(question.options)

    page.locator(".answer-button").nth(correct_idx).click()
    page2.locator(".answer-button").nth(wrong_idx).click()

    # Wait for match results
    expect(page.locator(".results-card")).to_be_visible(timeout=15000)
    expect(page.locator(".profile-history-status")).to_contain_text("saved", timeout=12000)

    # 5. Open Rankings on Ace's tab
    page.get_by_role("button", name="Rankings").click()
    expect(page.locator(".rankings-card h1")).to_be_visible()

    # Ace's personal rating banner should show rating 1016, PROVISIONAL, and delta +16
    expect(page.locator(".user-rating-banner")).to_be_visible()
    expect(page.locator(".user-rating-value")).to_contain_text("1016")
    expect(page.locator(".user-rating-meta")).to_contain_text("PROVISIONAL")
    expect(page.locator(".delta-pos").first).to_contain_text("+16")

    # Leaderboard table should list both Ace (Rank 1, 1016) and Rival (Rank 2, 984)
    expect(page.locator(".rankings-table")).to_be_visible()
    expect(page.locator(".rankings-table")).to_contain_text("Ranked Ace")
    expect(page.locator(".rankings-table")).to_contain_text("Ranked Rival")
    expect(page.locator(".rankings-table")).to_contain_text("1016")
    expect(page.locator(".rankings-table")).to_contain_text("984")
    expect(page.locator(".self-row")).to_contain_text("(You)")

    # 6. Mode filter check: Classic should show the match, Speed Challenge should show empty
    page.locator('.btn-scope[data-scope="classic"]').click()
    expect(page.locator(".rankings-table")).to_contain_text("Ranked Ace")
    expect(page.locator(".rankings-table")).to_contain_text("1016")

    with page.expect_response(lambda response: "/api/rankings?scope=mode&mode=speed_challenge" in response.url, timeout=15000) as speed_response:
        page.locator('.btn-scope[data-scope="speed_challenge"]').click()
    assert speed_response.value.status == 200
    expect(page.locator(".profile-empty")).to_be_visible()

    # 7. Player Profile view check: verify Elo rating section appears in Profile
    page.get_by_role("button", name="Player Profile").click()
    expect(page.locator(".profile-card h1")).to_contain_text("Ranked Ace")
    expect(page.locator(".user-rating-banner")).to_be_visible()
    expect(page.locator(".user-rating-value")).to_contain_text("1016")
    expect(page.locator(".profile-stats")).to_contain_text("1")
    expect(page.locator(".rating-history-row")).to_have_count(2)
    page.locator("#rating-history-scope").select_option("global")
    expect(page.locator(".rating-history-row")).to_have_count(1)
    expect(page.locator(".rating-history-row").first).to_contain_text("Global")
    page.locator("#rating-history-scope").select_option("")
    expect(page.locator(".rating-history-row")).to_have_count(2)

    # 8. Responsive layout on mobile viewport
    mobile_context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    mobile = mobile_context.new_page()
    mobile.set_default_timeout(10000)
    mobile.goto(origin, wait_until="domcontentloaded")
    mobile.get_by_role("button", name="Rankings").click()
    expect(mobile.locator(".rankings-card")).to_be_visible()
    expect(mobile.locator(".rankings-table")).to_be_visible()

    dims = mobile.evaluate("({width: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth})")
    assert dims["scroll"] <= dims["width"], f"Rankings page horizontally overflows on mobile: {dims}"

    print("PASS: rankings page navigation, scope switching, personal rating banner, authoritative Elo calculation and update (+16/-16), leaderboard display, mode filtering, profile rating card, and mobile layout.")


if __name__ == "__main__":
    main()
