"""Browser verification for Stage 8.4 Competitive League Seasons MVP."""

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
    with tempfile.TemporaryDirectory(prefix="quizverse-league-", dir=tmp_dir) as temporary:
        database_path = Path(temporary) / "leagues.sqlite3"
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
            raise RuntimeError(f"League migration failed: {migration.stderr[-2000:]}")

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
                raise RuntimeError(f"The league browser server did not start in 15s:\n{out}")

            verify_league_flow(origin)
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


def verify_league_flow(origin: str) -> None:
    with sync_playwright() as playwright:
        browser = (
            playwright.chromium.launch(channel="msedge", headless=True)
            if EDGE_PATH.exists()
            else playwright.chromium.launch(headless=True)
        )
        try:
            _verify_league_lifecycle(browser, origin)
        finally:
            browser.close()


def _verify_league_lifecycle(browser, origin: str) -> None:
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
    host_page.locator("#profile-create-name").fill("LeagueHost")
    host_page.locator('[data-action="profile-create"]').click()
    expect(host_page.locator(".profile-card h1")).to_contain_text("LeagueHost")

    # 3. Contender creates profile
    contender_page.goto(origin, wait_until="domcontentloaded")
    contender_page.get_by_role("button", name="Player Profile").click()
    contender_page.locator("#profile-create-name").fill("ContenderZack")
    contender_page.locator('[data-action="profile-create"]').click()
    expect(contender_page.locator(".profile-card h1")).to_contain_text("ContenderZack")

    # 4. Host opens Leagues navigation
    host_page.get_by_role("button", name="Leagues", exact=True).click()
    expect(host_page.locator(".leagues-card h1")).to_contain_text("Competitive Leagues")
    expect(host_page.locator(".section-kicker")).to_contain_text("STAGE 8.4 · SEASONAL LEAGUES")
    expect(host_page.locator(".profile-empty")).to_contain_text("No leagues found")

    # 5. Host creates a Classic League
    host_page.locator('[data-action="open-league-create"]').click()
    expect(host_page.locator(".create-modal-card h2")).to_contain_text("Create Competitive League")

    host_page.locator("#league-name").fill("Premier Classic League")
    host_page.locator("#league-mode").select_option("classic")
    host_page.locator("#league-desc").fill("Official Stage 8.4 Seasonal Round-Robin Competition")
    host_page.locator('[data-action="league-submit-create"]').click()

    # Detail screen opens
    expect(host_page.locator(".league-detail-banner h1")).to_contain_text("Premier Classic League")
    expect(host_page.locator('[data-action="open-season-create"]')).to_be_visible()

    # 6. Host creates Season 1
    host_page.locator('[data-action="open-season-create"]').click()
    expect(host_page.locator(".create-modal-card h2")).to_contain_text("Create Season")

    host_page.locator("#season-name").fill("Inaugural Season")
    host_page.locator("#season-capacity").select_option("4")
    host_page.locator('[data-action="season-submit-create"]').click()

    # Season detail screen opens
    expect(host_page.locator(".league-detail-banner h1")).to_contain_text("Season 1: Inaugural Season")
    expect(host_page.locator(".badge-draft")).to_contain_text("Draft")
    expect(host_page.locator('[data-action="open-season-reg"]')).to_be_visible()

    # 7. Host opens registration
    host_page.locator('[data-action="open-season-reg"]').click()
    expect(host_page.locator(".badge-open")).to_contain_text("Open Registration")
    expect(host_page.locator('[data-action="register-season"]')).to_be_visible()

    # 8. Host registers
    host_page.locator('[data-action="register-season"]').click()
    expect(host_page.locator('[data-action="withdraw-season"]')).to_be_visible()
    expect(host_page.locator(".standings-table")).to_contain_text("LeagueHost")

    # 9. Contender navigates to Leagues -> View League -> View Season & registers
    contender_page.get_by_role("button", name="Leagues", exact=True).click()
    expect(contender_page.locator(".league-card-title")).to_contain_text("Premier Classic League")
    contender_page.locator('[data-action="view-league"]').click()
    expect(contender_page.locator(".league-card-title")).to_contain_text("Season 1: Inaugural Season")
    contender_page.locator('[data-action="view-season"]').click()
    expect(contender_page.locator('[data-action="register-season"]')).to_be_visible()
    contender_page.locator('[data-action="register-season"]').click()
    expect(contender_page.locator('[data-action="withdraw-season"]')).to_be_visible()
    expect(contender_page.locator(".standings-table")).to_contain_text("ContenderZack")

    # 10. Host starts season
    expect(host_page.locator('[data-action="start-season"]')).to_be_visible()
    host_page.locator('[data-action="start-season"]').click()

    # Season is In Progress and round-robin schedule is rendered!
    expect(host_page.locator(".badge-in-progress")).to_contain_text("In Progress")
    expect(host_page.locator(".round-section")).to_have_count(1)  # 2 players = 1 round
    expect(host_page.locator(".fixture-card")).to_be_visible()
    expect(host_page.locator('[data-action="launch-season-fixture"]')).to_be_visible()

    # 11. Contender also sees the schedule and "Play Match"
    contender_page.get_by_role("button", name="Leagues", exact=True).click()
    if contender_page.locator('[data-action="view-league"]').is_visible():
        contender_page.locator('[data-action="view-league"]').click()
    if contender_page.locator('[data-action="view-season"]').is_visible():
        contender_page.locator('[data-action="view-season"]').click()
    expect(contender_page.locator('[data-action="launch-season-fixture"]')).to_be_visible()

    # 12. Host launches fixture match
    host_page.locator('[data-action="launch-season-fixture"]').click()
    expect(host_page.locator(".room-code")).to_be_visible(timeout=12000)

    # 13. Contender launches match and joins same room
    contender_page.locator('[data-action="launch-season-fixture"]').click()
    expect(contender_page.locator(".room-code")).to_be_visible(timeout=12000)

    expect(host_page.locator(".player-row")).to_have_count(2, timeout=10000)
    expect(contender_page.locator(".player-row")).to_have_count(2, timeout=10000)

    # 14. Start game from lobby
    host_page.locator('[data-action="start"]').click()
    expect(host_page.locator(".answer-button").first).to_be_visible(timeout=12000)
    expect(contender_page.locator(".answer-button").first).to_be_visible(timeout=12000)

    # Play questions until results appear
    last_prompt = ""
    for _ in range(15):
        if host_page.locator(".results-card").is_visible():
            break
        if not host_page.locator(".question-text").is_visible():
            time.sleep(0.5)
            continue
        prompt = host_page.locator(".question-text").inner_text().strip()
        if not prompt or prompt == last_prompt:
            time.sleep(0.5)
            continue
        question = next((item for item in QUESTIONS if item.prompt == prompt), None)
        if question is None:
            time.sleep(0.5)
            continue
        last_prompt = prompt
        correct_idx = question.answer
        wrong_idx = (correct_idx + 1) % len(question.options)

        host_page.locator(".answer-button").nth(correct_idx).click()
        contender_page.locator(".answer-button").nth(wrong_idx).click()

        time.sleep(3.0)

    # Wait for results card
    expect(host_page.locator(".results-card")).to_be_visible(timeout=15000)
    expect(host_page.locator(".profile-history-status")).to_contain_text("saved", timeout=12000)

    # 15. Host navigates back to Leagues -> View League -> View Season
    host_page.get_by_role("button", name="Leagues", exact=True).click()
    if host_page.locator('[data-action="view-league"]').is_visible():
        host_page.locator('[data-action="view-league"]').click()
    if host_page.locator('[data-action="view-season"]').is_visible():
        host_page.locator('[data-action="view-season"]').click()

    # Verify season completed and winner awarded points!
    expect(host_page.locator(".badge-completed")).to_contain_text("Completed")
    expect(host_page.locator(".fixture-card")).to_contain_text("Winner: LeagueHost")
    expect(host_page.locator(".standings-table")).to_contain_text("LeagueHost")
    expect(host_page.locator(".standings-table")).to_contain_text("3")  # 3 points for win

    # 16. Audit trail contains key lifecycle events
    expect(host_page.locator(".audit-row").first).to_be_visible()
    audit_text = host_page.locator(".audit-list").inner_text()
    assert "season_created" in audit_text
    assert "season_started" in audit_text
    assert "fixture_completed" in audit_text
    assert "season_completed" in audit_text

    # 17. Mobile responsive layout
    mobile_context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    mobile = mobile_context.new_page()
    mobile.set_default_timeout(10000)
    mobile.goto(origin, wait_until="domcontentloaded")
    mobile.get_by_role("button", name="Leagues", exact=True).click()
    expect(mobile.locator(".leagues-card")).to_be_visible()
    dims = mobile.evaluate("({width: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth})")
    assert dims["scroll"] <= dims["width"], f"Leagues page horizontally overflows on mobile: {dims}"

    print("PASS: League full browser verification: creation, season creation, open registration, 2-player registration, start season with round-robin schedule, fixture admission tickets, room launch, real match play, server-authoritative result processing, standings update with points, champion crowning, audit trail, and mobile responsiveness.")


if __name__ == "__main__":
    main()
