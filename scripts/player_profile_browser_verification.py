"""Browser verification for optional player profiles and persistent match history."""

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
    with tempfile.TemporaryDirectory(prefix="quizverse-player-profile-") as temporary:
        database_url = f"sqlite:///{(Path(temporary) / 'profiles.sqlite3').as_posix()}"
        environment = {**os.environ, "DATABASE_URL": database_url}
        migration = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=PROJECT_ROOT, env=environment, capture_output=True, text=True, check=False,
        )
        if migration.returncode:
            raise RuntimeError(f"Player-profile migration failed: {migration.stderr[-2000:]}")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        environment["ALLOWED_ORIGINS"] = origin
        log_path = Path(temporary) / "uvicorn.log"
        log_file = log_path.open("w+", encoding="utf-8")
        server_runner = (
            "import signal; from uvicorn import Config, Server; "
            f"server = Server(Config('quizverse.app:app', host='127.0.0.1', port={port})); "
            "signal.signal(signal.SIGBREAK, lambda *_: setattr(server, 'should_exit', True)); "
            "server.run()"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", server_runner],
            cwd=PROJECT_ROOT, env=environment, stdout=log_file, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        try:
            wait_for_server(origin, process)
            verify_profile_flow(origin)
        except Exception:
            log_file.flush()
            log_file.seek(0)
            print("Temporary server log:\n" + log_file.read()[-8000:], file=sys.stderr)
            raise
        finally:
            process.send_signal(signal.CTRL_BREAK_EVENT)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            log_file.close()


def wait_for_server(origin: str, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Uvicorn exited with code {process.returncode}")
        try:
            with urllib.request.urlopen(f"{origin}/health", timeout=0.5) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("The player-profile browser server did not start.")


def verify_profile_flow(origin: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True) if EDGE_PATH.exists() else playwright.chromium.launch(headless=True)
        try:
            _verify_profile_pages(browser, origin)
        finally:
            browser.close()


def _verify_profile_pages(browser, origin: str) -> None:
    desktop_context = browser.new_context(viewport={"width": 1280, "height": 900})
    desktop_context.grant_permissions(["clipboard-read", "clipboard-write"], origin=origin)
    page = desktop_context.new_page()
    page.set_default_timeout(8000)
    page.goto(origin, wait_until="domcontentloaded")
    page.get_by_role("button", name="Player Profile").click()
    expect(page.get_by_role("heading", name="Create profile")).to_be_visible()
    page.locator("#profile-create-name").fill("Nova Profile")
    with page.expect_response(lambda response: response.url.endswith("/api/player-profiles") and response.request.method == "POST") as created_response:
        page.locator('[data-action="profile-create"]').click()
    created = created_response.value.json()
    original_credential = created["credential"]
    expect(page.locator(".profile-card h1")).to_contain_text("Nova Profile")
    expect(page.locator(".profile-warning")).to_contain_text("cannot be recovered")
    assert original_credential not in page.locator("body").inner_text()

    page.get_by_role("button", name="Play", exact=True).click()
    page.locator("#player-name").fill("Nova")
    page.locator("#game-mode").select_option("solo_practice")
    page.locator(".competitive-settings summary").click()
    page.locator("#match-count").fill("1")
    page.locator('[data-action="create"]').click()
    # Solo Practice does not render a multiplayer room code.
    expect(page.locator('[data-action="start"]')).to_be_visible()
    page.locator('[data-action="start"]').click()
    expect(page.locator(".answer-button").first).to_be_visible(timeout=12000)
    prompt = page.locator(".question-text").inner_text()
    question = next(item for item in QUESTIONS if item.prompt == prompt)
    page.locator(".answer-button").nth(question.answer).click()
    expect(page.locator(".results-card")).to_be_visible(timeout=12000)
    expect(page.locator(".profile-history-status")).to_contain_text("saved", timeout=10000)
    page.get_by_role("button", name="Player Profile").click()
    expect(page.locator(".profile-stats")).to_contain_text("1")
    expect(page.locator(".profile-stats")).to_contain_text("0")
    expect(page.locator(".profile-match")).to_contain_text("practice")
    assert page.locator(".profile-stats").inner_text().lower().find("wins") >= 0

    with page.expect_response(lambda response: response.url.endswith("/credentials/rotate") and response.request.method == "POST") as rotated_response:
        page.locator('[data-action="profile-rotate"]').click()
    rotated = rotated_response.value.json()
    new_credential = rotated["credential"]
    assert new_credential != original_credential
    assert new_credential not in page.locator("body").inner_text()
    page.locator('[data-action="profile-copy-credential"]').click()
    assert page.evaluate("navigator.clipboard.readText()") == new_credential
    page.locator('[data-action="profile-logout"]').click()
    page.locator("#profile-credential").fill(new_credential)
    page.locator('[data-action="profile-signin"]').click()
    expect(page.locator(".profile-match")).to_contain_text("solo practice")
    assert page.evaluate("location.href").find(new_credential) == -1

    page.on("dialog", lambda dialog: dialog.accept())
    page.locator('[data-action="profile-delete"]').click()
    expect(page.get_by_role("heading", name="Create profile")).to_be_visible()
    rejected = page.request.get(f"{origin}/api/player-profiles/me", headers={"Authorization": f"Bearer {new_credential}"})
    assert rejected.status == 401

    mobile_context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    mobile = mobile_context.new_page()
    mobile.goto(origin, wait_until="domcontentloaded")
    mobile.get_by_role("button", name="Player Profile").click()
    expect(mobile.locator(".profile-card")).to_be_visible()
    dimensions = mobile.evaluate("({width: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth})")
    assert dimensions["scroll"] <= dimensions["width"], f"Profile view overflows on mobile: {dimensions}"
    print("PASS: optional pseudonymous profile creation, one-time credential copy and rotation, tab-scoped sign-in, server-bound Solo Practice identity, durable history, practice excluded from competitive wins, deletion/anonymization, invalidated credential, and mobile layout")
    browser.close()



if __name__ == "__main__":
    main()
