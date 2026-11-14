"""Browser verification for Stage 8.5 Advanced Competition & Career Hub."""

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

from quizverse.questions import QUESTIONS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EDGE_PATH = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


def main() -> None:
    tmp_dir = PROJECT_ROOT / "tmp"
    tmp_dir.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="quizverse-comp-", dir=tmp_dir) as temporary:
        database_path = Path(temporary) / "competition.sqlite3"
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
            raise RuntimeError(f"Competition migration failed: {migration.stderr[-2000:]}")

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
                raise RuntimeError(f"The competition browser server did not start in 15s:\n{out}")

            verify_competition_flow(origin)
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


def verify_competition_flow(origin: str) -> None:
    with sync_playwright() as playwright:
        browser = (
            playwright.chromium.launch(channel="msedge", headless=True)
            if EDGE_PATH.exists()
            else playwright.chromium.launch(headless=True)
        )
        try:
            _verify_career_and_h2h(browser, origin)
        finally:
            browser.close()


def _verify_career_and_h2h(browser, origin: str) -> None:
    # 1. Player 1 Context
    ctx1 = browser.new_context(viewport={"width": 1280, "height": 900})
    page1 = ctx1.new_page()
    page1.goto(origin)

    page1.get_by_role("button", name="Player Profile").click()
    expect(page1.get_by_role("heading", name="Create profile")).to_be_visible()
    page1.locator("#profile-create-name").fill("Grandmaster Ace")
    with page1.expect_response(lambda r: r.url.endswith("/api/player-profiles") and r.request.method == "POST") as res1:
        page1.locator('[data-action="profile-create"]').click()
    p1_data = res1.value.json()
    p1_id = p1_data["player"]["id"]
    expect(page1.locator(".profile-card h1")).to_contain_text("Grandmaster Ace")

    # 2. Player 2 Context
    ctx2 = browser.new_context(viewport={"width": 1280, "height": 900})
    page2 = ctx2.new_page()
    page2.goto(origin)

    page2.get_by_role("button", name="Player Profile").click()
    expect(page2.get_by_role("heading", name="Create profile")).to_be_visible()
    page2.locator("#profile-create-name").fill("Challenger Nova")
    with page2.expect_response(lambda r: r.url.endswith("/api/player-profiles") and r.request.method == "POST") as res2:
        page2.locator('[data-action="profile-create"]').click()
    p2_data = res2.value.json()
    p2_id = p2_data["player"]["id"]
    expect(page2.locator(".profile-card h1")).to_contain_text("Challenger Nova")

    # 3. Play a 1-round Classic Match between them
    page1.get_by_role("button", name="Play", exact=True).click()
    page1.locator("#player-name").fill("Ace")
    page1.locator("#game-mode").select_option("classic")
    page1.locator(".competitive-settings summary").click()
    page1.locator("#match-count").fill("1")
    page1.locator('[data-action="create"]').click()

    expect(page1.locator(".room-code")).to_be_visible()
    room_code = page1.locator(".room-code").inner_text().strip()

    # Player 2 joins
    page2.get_by_role("button", name="Play", exact=True).click()
    page2.locator("#player-name").fill("Nova")
    page2.locator('[data-action="join"]').click()
    page2.locator("#room-code").fill(room_code)
    page2.locator('[data-action="confirm-join"]').click()

    # Wait for Nova to appear in Ace's lobby
    expect(page1.locator(".player-row").nth(1)).to_be_visible()

    # Host starts the match
    expect(page1.locator('[data-action="start"]')).to_be_visible()
    page1.locator('[data-action="start"]').click()

    # Wait for question
    expect(page1.locator(".answer-button").first).to_be_visible(timeout=15000)
    expect(page2.locator(".answer-button").first).to_be_visible(timeout=15000)

    prompt = page1.locator(".question-text").inner_text()
    question = next(item for item in QUESTIONS if item.prompt == prompt)
    correct_idx = question.answer
    wrong_idx = (correct_idx + 1) % len(question.options)

    # Player 1 answers correctly, Player 2 answers incorrectly
    page1.locator(".answer-button").nth(correct_idx).click()
    page2.locator(".answer-button").nth(wrong_idx).click()

    # Results card
    expect(page1.locator(".results-card")).to_be_visible(timeout=15000)
    expect(page2.locator(".results-card")).to_be_visible(timeout=15000)
    expect(page1.locator(".profile-history-status")).to_contain_text("saved", timeout=12000)
    expect(page2.locator(".profile-history-status")).to_contain_text("saved", timeout=12000)

    # 4. Navigate back to Player 1 Career Hub
    page1.get_by_role("button", name="Player Profile").click()
    expect(page1.locator(".profile-card h1")).to_contain_text("Grandmaster Ace")

    # Verify Career Overview Grid
    expect(page1.locator(".career-overview-grid")).to_be_visible()
    expect(page1.locator(".career-stat-card").first).to_contain_text("100%")  # Win rate
    expect(page1.locator(".career-overview-grid")).to_contain_text("Clean")  # Fair play
    expect(page1.locator(".career-overview-grid")).to_contain_text("+1 WIN")  # Current streak
    expect(page1.locator(".career-overview-grid")).to_contain_text("Best Streak")

    # Verify Recent Form track
    expect(page1.locator(".form-guide-track")).to_be_visible()
    expect(page1.locator(".form-pill-win")).to_contain_text("W")

    # Verify Peak Rating & Elo banner
    expect(page1.locator(".user-rating-banner")).to_be_visible()
    expect(page1.locator(".user-rating-meta")).to_contain_text("PEAK")
    expect(page1.locator(".user-rating-meta")).to_contain_text("PROVISIONAL")

    # Verify Trophy Cabinet
    expect(page1.locator(".profile-card")).to_contain_text("Career Trophy Cabinet")

    # Verify Match History Opponents and "Vs" button
    expect(page1.locator(".profile-match")).to_be_visible()
    expect(page1.locator('[data-action="open-h2h"]')).to_be_visible()
    expect(page1.locator('[data-action="open-h2h"]')).to_contain_text("Vs Challenger Nova")

    # 5. Test Head-to-Head Compare from input bar
    page1.locator("#h2h-opponent-input").fill(p2_id)
    page1.locator('[data-action="profile-h2h-compare"]').click()

    # Verify H2H Modal appears
    expect(page1.locator(".h2h-modal")).to_be_visible()
    expect(page1.locator(".h2h-vs-header")).to_contain_text("Grandmaster Ace")
    expect(page1.locator(".h2h-vs-header")).to_contain_text("Challenger Nova")
    expect(page1.locator(".h2h-metrics-grid")).to_contain_text("1 - 0 - 0")  # Record
    expect(page1.locator(".h2h-metrics-grid")).to_contain_text("100% vs 0%")  # Win rate
    expect(page1.locator(".h2h-matches-list")).to_contain_text("Classic")
    expect(page1.locator(".h2h-matches-list")).to_contain_text("Win")

    # Close modal
    page1.locator('button[data-action="h2h-close"]').click()
    expect(page1.locator(".h2h-modal")).not_to_be_visible()

    # 6. Test Head-to-Head Compare from match history "Vs" button
    page1.locator('[data-action="open-h2h"]').first.click()
    expect(page1.locator(".h2h-modal")).to_be_visible()
    expect(page1.locator(".h2h-vs-header")).to_contain_text("Grandmaster Ace")
    expect(page1.locator(".h2h-vs-header")).to_contain_text("Challenger Nova")

    # Close modal via backdrop click
    page1.locator(".h2h-modal-backdrop").click(position={"x": 10, "y": 10})
    expect(page1.locator(".h2h-modal")).not_to_be_visible()

    # 7. Mobile Viewport Verification
    mobile = browser.new_context(viewport={"width": 390, "height": 844})
    mobile_page = mobile.new_page()
    mobile_page.goto(origin)
    mobile_page.get_by_role("button", name="Player Profile").click()
    expect(mobile_page.locator(".profile-card")).to_be_visible()

    # Verify responsive dimensions - no horizontal scrolling overflow
    dims = mobile_page.evaluate("({width: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth})")
    assert dims["scroll"] <= dims["width"] + 2, f"Mobile overflow detected: {dims}"

    mobile.close()
    ctx1.close()
    ctx2.close()


if __name__ == "__main__":
    main()
