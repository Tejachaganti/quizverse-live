"""Playwright browser verification for Stage 9.3 Casual Direct Rematches."""

from __future__ import annotations

import datetime
import os
import secrets
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


def seed_completed_match(database_url: str, p1_id: str, p2_id: str) -> str:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from quizverse.persistence.models import CompetitiveMatch, MatchParticipant, utc_now

    engine = create_engine(database_url)
    match_id = secrets.token_hex(16)
    now = utc_now()
    with Session(engine) as session:
        match = CompetitiveMatch(
            id=match_id,
            engine_match_id=f"eng_{match_id}",
            mode="classic",
            status="completed",
            result_kind="individual",
            rules_version="v1.0",
            started_at=now - datetime.timedelta(minutes=10),
            completed_at=now,
            question_set_hash="a" * 64,
            result_digest="b" * 64,
            result_context={},
            is_rated=True,
        )
        session.add(match)
        session.flush()

        mp1 = MatchParticipant(
            id=secrets.token_hex(16),
            match_id=match_id,
            player_id=p1_id,
            seat_index=0,
            final_score=100,
            correct_count=8,
            answer_count=10,
            placement=1,
            outcome="win",
            forfeited=False,
        )
        mp2 = MatchParticipant(
            id=secrets.token_hex(16),
            match_id=match_id,
            player_id=p2_id,
            seat_index=1,
            final_score=80,
            correct_count=6,
            answer_count=10,
            placement=2,
            outcome="loss",
            forfeited=False,
        )
        session.add(mp1)
        session.add(mp2)
        session.commit()
    return match_id


def main() -> None:
    tmp_dir = PROJECT_ROOT / "tmp"
    tmp_dir.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="quizverse-rematches-", dir=tmp_dir, ignore_cleanup_errors=True) as temporary:
        database_path = Path(temporary) / "rematches.sqlite3"
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
                raise RuntimeError(f"The rematches browser server did not start in 15s:\n{out}")

            verify_rematches_flow(origin, database_url)
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


def verify_rematches_flow(origin: str, database_url: str) -> None:
    with sync_playwright() as playwright:
        browser = (
            playwright.chromium.launch(channel="msedge", headless=True)
            if EDGE_PATH.exists()
            else playwright.chromium.launch(headless=True)
        )
        try:
            _run_rematch_checks(browser, origin, database_url)
        finally:
            browser.close()


def _run_rematch_checks(browser, origin: str, database_url: str) -> None:
    # 1. Player Challenger Context
    ctx_challenger = browser.new_context(viewport={"width": 1280, "height": 900})
    page_c = ctx_challenger.new_page()
    page_c.goto(origin)

    page_c.get_by_role("button", name="Player Profile").click()
    page_c.locator("#profile-create-name").fill("RematchAlice")
    with page_c.expect_response(lambda r: r.url.endswith("/api/player-profiles") and r.request.method == "POST") as res_c:
        page_c.locator('[data-action="profile-create"]').click()
    id_c = res_c.value.json()["player"]["id"]
    expect(page_c.locator(".profile-card h1")).to_contain_text("RematchAlice")

    # 2. Player Opponent Context
    ctx_opponent = browser.new_context(viewport={"width": 1280, "height": 900})
    page_o = ctx_opponent.new_page()
    page_o.goto(origin)

    page_o.get_by_role("button", name="Player Profile").click()
    page_o.locator("#profile-create-name").fill("RematchBob")
    with page_o.expect_response(lambda r: r.url.endswith("/api/player-profiles") and r.request.method == "POST") as res_o:
        page_o.locator('[data-action="profile-create"]').click()
    id_o = res_o.value.json()["player"]["id"]
    expect(page_o.locator(".profile-card h1")).to_contain_text("RematchBob")

    # 3. Seed completed casual match between Alice and Bob
    match_id = seed_completed_match(database_url, id_c, id_o)

    # 4. Challenger refreshes Profile to see match history
    page_c.get_by_role("button", name="Player Profile").click()
    expect(page_c.locator(".history-item")).to_be_visible(timeout=6000)
    expect(page_c.locator('[data-action="challenge-rematch"]')).to_be_visible()

    # 5. Challenger clicks "Rematch"
    page_c.locator('[data-action="challenge-rematch"]').first.click()
    expect(page_c.locator("#toast")).to_contain_text("Rematch challenge issued", timeout=6000)

    # 6. Verify Outgoing Rematch card in Challenger profile
    expect(page_c.locator(".rematch-section")).to_be_visible()
    expect(page_c.locator(".rematch-card")).to_contain_text("Waiting for opponent", timeout=6000)

    # 7. Opponent views Profile -> sees Incoming Rematch challenge
    page_o.get_by_role("button", name="Player Profile").click()
    expect(page_o.locator(".rematch-card")).to_contain_text("RematchAlice", timeout=6000)
    expect(page_o.locator('[data-action="accept-rematch"]')).to_be_visible()

    # 8. Opponent clicks "Accept Rematch" -> Enters private arena lobby
    page_o.locator('[data-action="accept-rematch"]').first.click()
    expect(page_o.locator("#toast")).to_contain_text("Rematch arena created", timeout=8000)
    expect(page_o.locator(".room-layout")).to_be_visible()

    # 9. Real-time accepted event: Challenger auto-joins or enters arena!
    expect(page_c.locator(".room-layout")).to_be_visible(timeout=10000)
    print("Stage 9.3 Rematches Playwright verification completed successfully!")


if __name__ == "__main__":
    main()
