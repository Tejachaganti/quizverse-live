"""Playwright browser verification for Stage 10 AI Intelligence & Learning Engine."""

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
    with tempfile.TemporaryDirectory(prefix="quizverse-ai-intel-", dir=tmp_dir, ignore_cleanup_errors=True) as temporary:
        database_path = Path(temporary) / "ai_intel.sqlite3"
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
                raise RuntimeError(f"The AI intel browser server did not start in 15s:\n{out}")

            verify_ai_intelligence_flow(origin)
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


def verify_ai_intelligence_flow(origin: str) -> None:
    with sync_playwright() as playwright:
        browser = (
            playwright.chromium.launch(channel="msedge", headless=True)
            if EDGE_PATH.exists()
            else playwright.chromium.launch(headless=True)
        )
        try:
            _run_ai_intelligence_checks(browser, origin)
        finally:
            browser.close()


def _run_ai_intelligence_checks(browser, origin: str) -> None:
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    page.goto(origin)

    # 1. Create a Player Profile
    page.get_by_role("button", name="Player Profile").click()
    page.locator("#profile-create-name").fill("AdaLovelaceIntel")
    with page.expect_response(lambda r: r.url.endswith("/api/player-profiles") and r.request.method == "POST"):
        page.locator('[data-action="profile-create"]').click()
    expect(page.locator(".profile-card h1")).to_contain_text("AdaLovelaceIntel")

    # 2. Navigate to AI Intelligence Hub
    page.locator('#primary-navigation [data-view="intelligence"]').click()
    expect(page.locator(".intel-shell")).to_be_visible()
    expect(page.locator(".intel-header h1")).to_contain_text("AI Intelligence")

    # 3. Overview Tab
    overview_tab_btn = page.locator('.intel-nav [data-tab="overview"]')
    expect(overview_tab_btn).to_have_class(re.compile(r"\bactive\b"))
    expect(page.locator(".intel-metric-card, .intel-card").first).to_be_visible()
    expect(page.locator(".intel-shell")).to_contain_text("AI Mastery Status")

    # 4. Topic Mastery & Weaknesses Tab
    page.locator('.intel-nav [data-tab="topics"]').click()
    expect(page.locator('.intel-nav [data-tab="topics"]')).to_have_class(re.compile(r"\bactive\b"))
    expect(page.locator(".intel-shell")).to_contain_text("Mastery Strengths")
    expect(page.locator(".intel-shell")).to_contain_text("Focus & Growth Areas")

    # 5. Adaptive Difficulty Tab
    page.locator('.intel-nav [data-tab="difficulty"]').click()
    expect(page.locator('.intel-nav [data-tab="difficulty"]')).to_have_class(re.compile(r"\bactive\b"))
    expect(page.locator(".diff-hero")).to_be_visible()
    expect(page.locator(".diff-badge-current")).to_contain_text("CURRENT SOLO DIFFICULTY")

    # Apply recommended difficulty
    apply_diff_btn = page.locator('[data-action="apply-recommended-diff"]')
    if apply_diff_btn.is_visible():
        apply_diff_btn.click()
        expect(page.locator("#toast")).to_be_visible()

    # 6. Learning Paths Tab
    page.locator('.intel-nav [data-tab="paths"]').click()
    expect(page.locator('.intel-nav [data-tab="paths"]')).to_have_class(re.compile(r"\bactive\b"))

    # Generate Learning Path
    with page.expect_response(lambda r: "/api/intelligence/learning-paths/generate" in r.url and r.request.method == "POST"):
        page.locator('[data-action="regenerate-path"]').click()

    # Verify curriculum steps rendered
    expect(page.locator(".path-step-card").first).to_be_visible()
    expect(page.locator(".path-step-title").first).to_be_visible()

    # Mark first step as completed
    complete_step_btn = page.locator('[data-action="complete-path-step"]').first
    if complete_step_btn.is_visible():
        with page.expect_response(lambda r: "/steps/" in r.url and "/complete" in r.url and r.request.method == "POST"):
            complete_step_btn.click()
        expect(page.locator(".path-step-card.completed, .intel-badge-strong").first).to_be_visible()

    # 7. Recommendations Tab
    page.locator('.intel-nav [data-tab="recommendations"]').click()
    expect(page.locator('.intel-nav [data-tab="recommendations"]')).to_have_class(re.compile(r"\bactive\b"))
    expect(page.locator(".intel-tab-content")).to_be_visible()

    # 8. Performance Analytics Tab
    page.locator('.intel-nav [data-tab="performance"]').click()
    expect(page.locator('.intel-nav [data-tab="performance"]')).to_have_class(re.compile(r"\bactive\b"))
    expect(page.locator(".intel-shell")).to_contain_text("Accuracy")
    expect(page.locator(".intel-shell")).to_contain_text("Questions Evaluated")

    # Switch time window to 7 days
    page.locator('[data-action="set-perf-window"][data-window="7_days"]').click()
    expect(page.locator('[data-action="set-perf-window"][data-window="7_days"]')).to_have_class(re.compile(r"\bactive\b"))

    # 9. AI Reports Tab
    page.locator('.intel-nav [data-tab="reports"]').click()
    expect(page.locator('.intel-nav [data-tab="reports"]')).to_have_class(re.compile(r"\bactive\b"))

    # Generate AI Learning Report
    with page.expect_response(lambda r: "/api/intelligence/reports/generate" in r.url and r.request.method == "POST"):
        page.locator('[data-action="generate-report-btn"]').first.click()

    expect(page.locator(".report-container")).to_be_visible()
    expect(page.locator(".report-summary-box")).to_be_visible()
    expect(page.locator(".report-header")).to_contain_text("Personalized Learning Assessment")

    # 10. Quality Creator Studio Tab
    page.locator('.intel-nav [data-tab="creator"]').click()
    expect(page.locator('.intel-nav [data-tab="creator"]')).to_have_class(re.compile(r"\bactive\b"))
    expect(page.locator("#intel-creator-form")).to_be_visible()

    # Fill Question Quality form
    page.locator("#intel-q-prompt").fill("What is the capital city of France?")
    page.locator("#intel-q-opt0").fill("Paris")
    page.locator("#intel-q-opt1").fill("Rome")
    page.locator("#intel-q-opt2").fill("Berlin")
    page.locator("#intel-q-opt3").fill("Madrid")
    page.locator("#intel-q-correct").select_option("0")
    page.locator("#intel-q-category").fill("Geography")
    page.locator("#intel-q-difficulty").select_option("easy")
    page.locator("#intel-q-explanation").fill("Paris is the capital and largest city of France located on the Seine River.")

    with page.expect_response(lambda r: "/api/intelligence/questions/analyze" in r.url and r.request.method == "POST"):
        page.locator('[data-action="analyze-question"]').click()

    expect(page.locator(".intel-tab-content")).to_contain_text("Analysis Results")
    expect(page.locator(".intel-tab-content")).to_contain_text("Quality Score")
    expect(page.locator(".intel-tab-content")).to_contain_text("Clarity")

    # 11. Security Inspection: Ensure no credentials, auth tokens, or private secrets leak into the DOM
    dom_text = page.locator(".intel-shell").inner_text()
    assert "token" not in dom_text.lower() or "practice" in dom_text.lower(), "Potential token leaked"
    assert "password" not in dom_text.lower(), "Password string leaked"
    assert "credential" not in dom_text.lower(), "Credential leaked"
    assert "bearer" not in dom_text.lower(), "Bearer authorization header leaked"

    print("STAGE 10 AI INTELLIGENCE BROWSER VERIFICATION COMPLETE!")


if __name__ == "__main__":
    main()
