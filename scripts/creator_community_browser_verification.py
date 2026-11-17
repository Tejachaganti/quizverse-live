"""Playwright browser verification for Stage 11 Creator & Community Ecosystem."""

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
    with tempfile.TemporaryDirectory(prefix="quizverse-comm-", dir=tmp_dir, ignore_cleanup_errors=True) as temporary:
        database_path = Path(temporary) / "comm_test.sqlite3"
        database_url = f"sqlite:///{database_path.as_posix()}"
        environment = {
            **os.environ,
            "DATABASE_URL": database_url,
            "PRESENCE_GRACE_PERIOD": "2.0",
        }

        # 1. Run Alembic upgrade head
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

        # 2. Bind port
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
                raise RuntimeError(f"The Community browser server did not start in 15s:\n{out}")

            verify_creator_community_flow(origin)
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


def verify_creator_community_flow(origin: str) -> None:
    with sync_playwright() as playwright:
        browser = (
            playwright.chromium.launch(channel="msedge", headless=True)
            if EDGE_PATH.exists()
            else playwright.chromium.launch(headless=True)
        )
        try:
            _run_community_checks(browser, origin)
        finally:
            browser.close()


def _run_community_checks(browser, origin: str) -> None:
    # Session 1: Alice (Creator)
    context_alice = browser.new_context(viewport={"width": 1280, "height": 900})
    page_alice = context_alice.new_page()
    page_alice.goto(origin)

    # 1. Create Alice's profile
    page_alice.get_by_role("button", name="Player Profile").click()
    page_alice.locator("#profile-create-name").fill("AliceCreator")
    with page_alice.expect_response(lambda r: r.url.endswith("/api/player-profiles") and r.request.method == "POST"):
        page_alice.locator('[data-action="profile-create"]').click()
    expect(page_alice.locator(".profile-card h1")).to_contain_text("AliceCreator")

    # 2. Navigate to Community Hub
    page_alice.locator('#primary-navigation [data-view="community"]').click()
    expect(page_alice.locator(".comm-shell")).to_be_visible()
    expect(page_alice.locator(".comm-header h1")).to_contain_text("Creator & Community Ecosystem")

    # 3. Discover tab trays
    expect(page_alice.locator('.comm-tab-btn[data-tab="discover"]')).to_have_class(re.compile(r"\bactive\b"))
    expect(page_alice.locator(".comm-tab-content")).to_be_visible()

    # 4. Explore tab search & filters
    page_alice.locator('.comm-tab-btn[data-tab="explore"]').click()
    expect(page_alice.locator('.comm-tab-btn[data-tab="explore"]')).to_have_class(re.compile(r"\bactive\b"))
    expect(page_alice.locator("#comm-search-input")).to_be_visible()
    expect(page_alice.locator("#comm-filter-difficulty")).to_be_visible()

    # 5. Creator Studio tab - Alice updates Creator Profile
    page_alice.locator('.comm-tab-btn[data-tab="studio"]').click()
    expect(page_alice.locator('.comm-tab-btn[data-tab="studio"]')).to_have_class(re.compile(r"\bactive\b"))
    expect(page_alice.locator(".comm-hero")).to_be_visible()

    # Edit profile
    page_alice.locator('[data-action="toggle-edit-creator-profile"]').first.click()
    expect(page_alice.locator("#comm-edit-profile-box")).to_be_visible()
    page_alice.locator("#comm-profile-bio").fill("Marine Biologist and Trivia Enthusiast.")
    page_alice.locator("#comm-profile-specialty").fill("Marine Biology")
    page_alice.locator("#comm-profile-website").fill("https://alice.ocean.org")
    with page_alice.expect_response(lambda r: "/api/community/creators/me" in r.url and r.request.method == "PUT"):
        page_alice.locator('[data-action="save-creator-profile"]').click()
    expect(page_alice.locator(".comm-hero-bio")).to_contain_text("Marine Biologist")

    # 6. Alice authors and publishes a quiz via Studio Quick Create
    page_alice.locator('[data-action="toggle-quick-author-form"]').first.click()
    expect(page_alice.locator("#comm-quick-author-form")).to_be_visible()

    # Validation: empty form rejected
    page_alice.locator('[data-action="studio-quick-create"]').click()
    expect(page_alice.locator("#toast")).to_be_visible()

    # Fill valid quiz with question
    page_alice.locator("#comm-new-quiz-title").fill("Deep Sea Creatures 101")
    page_alice.locator("#comm-new-quiz-desc").fill("Fascinating facts about the abyss.")
    page_alice.locator("#comm-new-quiz-category").fill("Oceanography")
    page_alice.locator("#comm-new-q1-text").fill("Which creature has three hearts and blue blood?")
    page_alice.locator("#comm-new-q1-a1").fill("Octopus")
    page_alice.locator("#comm-new-q1-a2").fill("Jellyfish")
    page_alice.locator("#comm-new-q1-a3").fill("Sea Turtle")
    page_alice.locator("#comm-new-q1-a4").fill("Giant Squid")

    with page_alice.expect_response(lambda r: "/api/community/quizzes/" in r.url and "/publish" in r.url and r.request.method == "POST"):
        page_alice.locator('[data-action="studio-quick-create"]').click()

    expect(page_alice.locator(".comm-card-title", has_text="Deep Sea Creatures 101")).to_be_visible()
    expect(page_alice.locator(".comm-badge-plays").first).to_be_visible()

    # Session 2: Bob (Player / Reviewer)
    context_bob = browser.new_context(viewport={"width": 1280, "height": 900})
    page_bob = context_bob.new_page()
    page_bob.goto(origin)

    # Bob creates profile
    page_bob.get_by_role("button", name="Player Profile").click()
    page_bob.locator("#profile-create-name").fill("BobExplorer")
    with page_bob.expect_response(lambda r: r.url.endswith("/api/player-profiles") and r.request.method == "POST"):
        page_bob.locator('[data-action="profile-create"]').click()
    expect(page_bob.locator(".profile-card h1")).to_contain_text("BobExplorer")

    # Bob navigates to Community Hub
    page_bob.locator('#primary-navigation [data-view="community"]').click()
    expect(page_bob.locator(".comm-shell")).to_be_visible()

    # 7. Bob follows Alice in Creators tab
    page_bob.locator('.comm-tab-btn[data-tab="creators"]').click()
    expect(page_bob.locator('.comm-tab-btn[data-tab="creators"]')).to_have_class(re.compile(r"\bactive\b"))
    expect(page_bob.locator(".comm-creator-card")).to_be_visible()

    # Click Follow
    follow_btn = page_bob.locator('[data-action="toggle-follow-creator"]').first
    with page_bob.expect_response(lambda r: "/api/community/creators/follow/" in r.url and r.request.method == "POST"):
        follow_btn.click()
    expect(page_bob.locator(".comm-badge-plays").first).to_contain_text("1 followers")

    # 8. Bob discovers Alice's quiz in Search & Explore
    page_bob.locator('.comm-tab-btn[data-tab="explore"]').click()
    page_bob.locator("#comm-search-input").fill("Deep Sea")
    with page_bob.expect_response(lambda r: "/api/community/quizzes?" in r.url):
        page_bob.locator('[data-action="exec-comm-search"]').click()
    expect(page_bob.locator(".comm-card-title", has_text="Deep Sea Creatures 101")).to_be_visible()

    # 9. Verified Review Requirement check: Bob tries to review before playing
    page_bob.locator('[data-action="open-quiz-reviews"]').first.click()
    expect(page_bob.locator("#comm-reviews-modal")).to_be_visible()
    page_bob.locator("#comm-review-text").fill("Looks like a great quiz!")
    page_bob.locator('[data-action="submit-quiz-review"]').click()
    # Toast notification warns must play quiz first
    expect(page_bob.locator("#toast")).to_be_visible()
    page_bob.locator('[data-action="close-quiz-reviews"]').click()

    # 10. Bob plays the quiz
    with page_bob.expect_response(lambda r: "/api/community/quizzes/" in r.url and "/play" in r.url and r.request.method == "POST"):
        page_bob.locator('[data-action="play-community-quiz"]').first.click()

    # Bob returns to community and submits verified review
    page_bob.locator('#primary-navigation [data-view="community"]').click()
    page_bob.locator('.comm-tab-btn[data-tab="explore"]').click()
    page_bob.locator('[data-action="open-quiz-reviews"]').first.click()
    expect(page_bob.locator("#comm-reviews-modal")).to_be_visible()
    page_bob.locator("#comm-review-text").fill("Incredible questions and verified facts!")
    with page_bob.expect_response(lambda r: "/api/community/quizzes/" in r.url and "/reviews" in r.url and r.request.method == "POST"):
        page_bob.locator('[data-action="submit-quiz-review"]').click()

    # Review appears in list
    expect(page_bob.locator(".comm-review-card")).to_be_visible()
    expect(page_bob.locator(".comm-review-body")).to_contain_text("Incredible questions and verified facts!")
    page_bob.locator('[data-action="close-quiz-reviews"]').click()

    # 11. Bob favorites the quiz
    page_bob.locator('[data-action="toggle-favorite"]').first.click()
    expect(page_bob.locator("#toast")).to_be_visible()

    # Verify Saved Quizzes tab
    page_bob.locator('.comm-tab-btn[data-tab="favorites"]').click()
    expect(page_bob.locator('.comm-tab-btn[data-tab="favorites"]')).to_have_class(re.compile(r"\bactive\b"))
    expect(page_bob.locator(".comm-card-title", has_text="Deep Sea Creatures 101")).to_be_visible()

    # 12. Collections / Playlists: Alice creates a playlist
    page_alice.locator('.comm-tab-btn[data-tab="collections"]').click()
    page_alice.locator('[data-action="toggle-new-collection-form"]').first.click()
    page_alice.locator("#comm-collection-title").fill("Abyssal Wonders")
    page_alice.locator("#comm-collection-desc").fill("Complete ocean depths playlist")
    with page_alice.expect_response(lambda r: "/api/community/collections" in r.url and r.request.method == "POST"):
        page_alice.locator('[data-action="submit-create-collection"]').click()
    expect(page_alice.locator(".comm-card-title", has_text="Abyssal Wonders")).to_be_visible()

    # 13. Safety & Reporting: Bob reports content
    page_bob.locator('.comm-tab-btn[data-tab="explore"]').click()
    page_bob.locator('[data-action="open-report-modal"]').first.click()
    expect(page_bob.locator("#comm-report-modal")).to_be_visible()
    page_bob.locator("#comm-report-details").fill("Suspicious question wording.")
    with page_bob.expect_response(lambda r: "/api/community/reports" in r.url and r.request.method == "POST"):
        page_bob.locator('[data-action="submit-community-report"]').click()
    expect(page_bob.locator("#toast")).to_be_visible()

    # Ordinary Bob does NOT have moderation tab
    expect(page_bob.locator('.comm-tab-btn[data-tab="moderation"]')).not_to_be_visible()

    # 14. DOM Security Inspection: assert no sensitive credentials or tokens leak
    dom_content = page_bob.content()
    assert "profileCredential" not in dom_content
    assert "Authorization: Bearer" not in dom_content

    context_alice.close()
    context_bob.close()
    print("STAGE 11 CREATOR & COMMUNITY BROWSER VERIFICATION COMPLETE!")


if __name__ == "__main__":
    main()
