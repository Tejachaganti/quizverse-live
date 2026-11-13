"""Browser verification for Stage 8.3 Single-Elimination Tournament MVP."""

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
    with tempfile.TemporaryDirectory(prefix="quizverse-tournament-", dir=tmp_dir) as temporary:
        database_path = Path(temporary) / "tournaments.sqlite3"
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
            raise RuntimeError(f"Tournament migration failed: {migration.stderr[-2000:]}")

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
                raise RuntimeError(f"The tournament browser server did not start in 15s:\n{out}")

            verify_tournament_flow(origin)
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


def verify_tournament_flow(origin: str) -> None:
    with sync_playwright() as playwright:
        browser = (
            playwright.chromium.launch(channel="msedge", headless=True)
            if EDGE_PATH.exists()
            else playwright.chromium.launch(headless=True)
        )
        try:
            _verify_tournament_lifecycle(browser, origin)
        finally:
            browser.close()


def _verify_tournament_lifecycle(browser, origin: str) -> None:
    # 1. Host context
    host_context = browser.new_context(viewport={"width": 1280, "height": 900})
    host_page = host_context.new_page()
    host_page.set_default_timeout(10000)

    # Contender context
    contender_context = browser.new_context(viewport={"width": 1280, "height": 900})
    contender_page = contender_context.new_page()
    contender_page.set_default_timeout(10000)

    # 2. Host creates profile
    host_page.goto(origin, wait_until="domcontentloaded")
    host_page.get_by_role("button", name="Player Profile").click()
    host_page.locator("#profile-create-name").fill("HostAce")
    host_page.locator('[data-action="profile-create"]').click()
    expect(host_page.locator(".profile-card h1")).to_contain_text("HostAce")

    # 3. Contender creates profile
    contender_page.goto(origin, wait_until="domcontentloaded")
    contender_page.get_by_role("button", name="Player Profile").click()
    contender_page.locator("#profile-create-name").fill("ChallengerBob")
    contender_page.locator('[data-action="profile-create"]').click()
    expect(contender_page.locator(".profile-card h1")).to_contain_text("ChallengerBob")

    # 4. Host opens Tournaments navigation
    host_page.get_by_role("button", name="Tournaments", exact=True).click()
    expect(host_page.locator(".tournaments-card h1")).to_contain_text("Tournament Arena")
    expect(host_page.locator(".section-kicker")).to_contain_text("STAGE 8.3 · COMPETITIVE ARENA")
    expect(host_page.locator(".profile-empty")).to_contain_text("No tournaments found")

    # 5. Host creates a 2-player Classic tournament
    host_page.locator('[data-action="open-tournament-create"]').click()
    expect(host_page.locator(".create-modal-card h2")).to_contain_text("Create Tournament")

    host_page.locator("#tourn-name").fill("Winter Knockout MVP")
    host_page.locator("#tourn-mode").select_option("classic")
    host_page.locator("#tourn-capacity").select_option("4")
    host_page.locator("#tourn-desc").fill("Official Stage 8.3 Single-Elimination Knockout Championship")
    host_page.locator('[data-action="tournament-submit-create"]').click()

    # App immediately opens tournament detail screen
    expect(host_page.locator(".tournament-detail-banner h1")).to_contain_text("Winter Knockout MVP")
    expect(host_page.locator(".badge-draft")).to_contain_text("Draft")
    expect(host_page.locator('[data-action="open-tournament-reg"]')).to_be_visible()

    # 6. Host opens registration
    host_page.locator('[data-action="open-tournament-reg"]').click()
    expect(host_page.locator(".badge-open")).to_contain_text("Open Registration")
    expect(host_page.locator('[data-action="register-tournament"]')).to_be_visible()

    # 7. Host registers
    host_page.locator('[data-action="register-tournament"]').click()
    expect(host_page.locator('[data-action="withdraw-tournament"]')).to_be_visible()
    expect(host_page.locator(".rankings-table")).to_contain_text("HostAce")

    # 8. Contender opens Tournaments and registers
    contender_page.get_by_role("button", name="Tournaments", exact=True).click()
    expect(contender_page.locator(".tournament-card-title")).to_contain_text("Winter Knockout MVP")
    contender_page.locator('[data-action="view-tournament"]').click()
    expect(contender_page.locator('[data-action="register-tournament"]')).to_be_visible()
    contender_page.locator('[data-action="register-tournament"]').click()
    expect(contender_page.locator('[data-action="withdraw-tournament"]')).to_be_visible()
    expect(contender_page.locator(".rankings-table")).to_contain_text("ChallengerBob")

    # 9. Host starts tournament
    expect(host_page.locator('[data-action="start-tournament"]')).to_be_visible()
    host_page.locator('[data-action="start-tournament"]').click()

    # Tournament is now In Progress and bracket is rendered!
    expect(host_page.locator(".badge-in-progress")).to_contain_text("In Progress")
    expect(host_page.locator(".bracket-round-column")).to_have_count(1)  # 2 players = 1 round
    expect(host_page.locator(".bracket-match-node")).to_be_visible()
    expect(host_page.locator('[data-action="launch-tournament-match"]')).to_be_visible()

    # 10. Contender also views bracket and sees "Play Match"
    contender_page.get_by_role("button", name="Tournaments", exact=True).click()
    if contender_page.locator('[data-action="view-tournament"]').is_visible():
        contender_page.locator('[data-action="view-tournament"]').click()
    expect(contender_page.locator('[data-action="launch-tournament-match"]')).to_be_visible()

    # 11. Host launches match
    host_page.locator('[data-action="launch-tournament-match"]').click()
    expect(host_page.locator(".room-code")).to_be_visible(timeout=12000)

    # 12. Contender launches match and joins the same room
    contender_page.locator('[data-action="launch-tournament-match"]').click()
    expect(contender_page.locator(".room-code")).to_be_visible(timeout=12000)

    # Verify both players appear in lobby
    expect(host_page.locator(".player-row")).to_have_count(2, timeout=10000)
    expect(contender_page.locator(".player-row")).to_have_count(2, timeout=10000)

    # 13. Start game from lobby
    host_page.locator('[data-action="start"]').click()
    expect(host_page.locator(".answer-button").first).to_be_visible(timeout=12000)
    expect(contender_page.locator(".answer-button").first).to_be_visible(timeout=12000)

    # Play 5 questions
    for _ in range(5):
        prompt = host_page.locator(".question-text").inner_text()
        question = next(item for item in QUESTIONS if item.prompt == prompt)
        correct_idx = question.answer
        wrong_idx = (correct_idx + 1) % len(question.options)

        host_page.locator(".answer-button").nth(correct_idx).click()
        contender_page.locator(".answer-button").nth(wrong_idx).click()

        time.sleep(1.0)
        if host_page.locator(".results-card").is_visible():
            break

    # Wait for results card
    expect(host_page.locator(".results-card")).to_be_visible(timeout=15000)
    expect(host_page.locator(".profile-history-status")).to_contain_text("saved", timeout=12000)

    # 14. Host navigates back to Tournaments -> View Bracket
    host_page.get_by_role("button", name="Tournaments", exact=True).click()
    if host_page.locator('[data-action="view-tournament"]').is_visible():
        host_page.locator('[data-action="view-tournament"]').click()

    # Verify tournament completed and champion crowned!
    expect(host_page.locator(".badge-completed")).to_contain_text("Completed")
    expect(host_page.locator(".bracket-match-node")).to_contain_text("Completed · Winner: HostAce")
    expect(host_page.locator(".rankings-table")).to_contain_text("champion")
    expect(host_page.locator(".rankings-table")).to_contain_text("runner_up")

    # 15. Audit trail contains key lifecycle events
    expect(host_page.locator(".audit-row").first).to_be_visible()
    audit_text = host_page.locator(".audit-list").inner_text()
    assert "tournament_created" in audit_text
    assert "tournament_started" in audit_text
    assert "tournament_completed" in audit_text

    # 16. Mobile responsive layout
    mobile_context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    mobile = mobile_context.new_page()
    mobile.set_default_timeout(10000)
    mobile.goto(origin, wait_until="domcontentloaded")
    mobile.get_by_role("button", name="Tournaments", exact=True).click()
    expect(mobile.locator(".tournaments-card")).to_be_visible()
    dims = mobile.evaluate("({width: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth})")
    assert dims["scroll"] <= dims["width"], f"Tournaments page horizontally overflows on mobile: {dims}"

    print("PASS: Tournament full browser verification: creation, open registration, 2-player registration, start bracket generation, fixture admission tickets, room launch, real match play, server-authoritative result processing, winner advancement, champion/runner-up crowning, audit trail, and mobile responsiveness.")


if __name__ == "__main__":
    main()
