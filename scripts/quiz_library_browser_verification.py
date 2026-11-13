"""Browser-level verification of the quiz library against a temporary SQLite app."""

from __future__ import annotations

import os
from contextlib import ExitStack
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, expect, sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EDGE_PATH = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


def main() -> None:
    (PROJECT_ROOT / "tmp").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="quizverse-ui-", dir=PROJECT_ROOT / "tmp") as temporary_directory:
        database_path = Path(temporary_directory) / "quizverse-ui.sqlite3"
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
            raise RuntimeError(f"Temporary SQLite migration failed: {migration.stderr[-2000:]}")

        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        environment["ALLOWED_ORIGINS"] = origin
        server_log_path = Path(temporary_directory) / "uvicorn.log"
        server_log = server_log_path.open("w+", encoding="utf-8")
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "scripts.quiz_library_mock_app:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=PROJECT_ROOT,
            env=environment,
            stdout=server_log,
            stderr=subprocess.STDOUT,
        )
        try:
            wait_for_server(origin, process)
            verify_browser_flows(origin)
        except Exception:
            server_log.flush()
            server_log.seek(0)
            print("Temporary server log:\n" + server_log.read()[-6000:], file=sys.stderr)
            raise
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            server_log.close()
            # SQLite file handles may be released just after Windows reports
            # the server process exited; wait briefly before TemporaryDirectory
            # removes the isolated test database.
            for _attempt in range(20):
                try:
                    database_path.unlink(missing_ok=True)
                    break
                except PermissionError:
                    time.sleep(0.1)


def wait_for_server(origin: str, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Temporary Uvicorn process exited with code {process.returncode}.")
        try:
            with urllib.request.urlopen(f"{origin}/health", timeout=0.5) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.08)
    raise RuntimeError("Temporary FastAPI server did not become healthy.")


def verify_browser_flows(origin: str) -> None:
    with sync_playwright() as playwright, ExitStack() as cleanup:
        browser = (
            playwright.chromium.launch(channel="msedge", headless=True)
            if EDGE_PATH.exists()
            else playwright.chromium.launch(headless=True)
        )
        cleanup.enter_context(browser)
        owner_context: BrowserContext = browser.new_context(viewport={"width": 1365, "height": 900})
        guest_context: BrowserContext = browser.new_context(
            viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True
        )
        tablet_context: BrowserContext = browser.new_context(viewport={"width": 820, "height": 1024})
        owner = owner_context.new_page()
        guest = guest_context.new_page()
        tablet = tablet_context.new_page()
        owner_requests: list[tuple[str, str]] = []
        guest_requests: list[tuple[str, str]] = []
        owner_api_errors: list[tuple[int, object]] = []
        for page in (owner, guest, tablet):
            page.set_default_timeout(7000)
        owner.on("request", lambda request: owner_requests.append((request.url, request.headers.get("authorization", ""))))
        guest.on("request", lambda request: guest_requests.append((request.url, request.headers.get("authorization", ""))))
        owner.on("response", lambda response: capture_api_error(response, owner_api_errors))

        owner.goto(origin, wait_until="domcontentloaded")
        owner.keyboard.press("Tab")
        assert owner.locator(":focus-visible").count() > 0, "Keyboard tab focus is not visibly indicated."
        owner.locator('[data-view="library"]').focus()
        owner.keyboard.press("Enter")
        expect(owner.get_by_role("heading", name="Find your next great question.")).to_be_visible()
        assert_no_horizontal_overflow(owner)

        owner.get_by_role("tab", name="My quizzes").click()
        owner.get_by_role("button", name="Use or create an owner key").click()
        expect(owner.get_by_text("no password reset or account recovery", exact=False)).to_be_visible()
        warning_checkbox = owner.locator("#owner-key-acknowledged")
        create_key_button = owner.get_by_role("button", name="Create a new owner key")
        assert create_key_button.is_disabled(), "Owner key must require acknowledging the recovery warning."
        warning_checkbox.check()
        create_key_button.click()
        expect(owner.locator("#one-time-owner-key")).to_be_visible()
        owner_key = owner.locator("#one-time-owner-key").input_value()
        assert len(owner_key) >= 40
        owner.get_by_role("button", name="I saved it — continue").click()
        expect(owner.get_by_role("heading", name="Your library is ready for its first question")).to_be_visible()
        owner.get_by_role("button", name="Create your first quiz").click()

        owner.locator('[data-quiz-field="title"]').fill("Stage Four Orbit Quiz")
        owner.locator('[data-quiz-field="description"]').fill("A browser-verified science quiz.")
        owner.locator('[data-quiz-field="category"]').fill("science")
        owner.locator('[data-quiz-field="visibility"]').select_option("public")
        owner.locator('[data-quiz-action="add-question"]').click()
        owner.locator('[data-quiz-action="add-question"]').click()
        fill_question(
            owner.locator(".question-editor").nth(0),
            "First: Which planet is known as the Red Planet?",
            ["Venus", "Mars", "Earth", "Jupiter"],
            1,
            "Mars looks red because of iron oxide.",
        )
        fill_question(
            owner.locator(".question-editor").nth(1),
            "Second: Which star is closest to Earth?",
            ["Sirius", "The Sun", "Vega", "Polaris"],
            1,
            "The Sun is our closest star.",
        )

        # Client-side validation blocks malformed choices before a write.
        owner.locator(".question-editor").nth(1).locator('[data-option-index="3"]').fill("Sirius")
        owner.get_by_role("button", name="Save draft").click()
        expect(owner.get_by_role("alert")).to_contain_text("answer options must be distinct")
        owner.locator(".question-editor").nth(1).locator('[data-option-index="3"]').fill("Polaris")

        # Reordering is keyboard-operable and persists in the API question order.
        move_up = owner.locator(".question-editor").nth(1).get_by_role("button", name="Move question 2 up")
        move_up.focus()
        owner.keyboard.press("Enter")
        expect(owner.locator(".question-editor").first.locator('[data-question-field="prompt"]')).to_have_value(
            "Second: Which star is closest to Earth?"
        )
        owner.get_by_role("button", name="Preview", exact=True).click()
        expect(owner.locator(".preview-question").first.locator("h2")).to_have_text(
            "Second: Which star is closest to Earth?"
        )
        preview_text = owner.locator(".preview-view").inner_text()
        assert "The Sun is our closest star." not in preview_text
        assert "correct_option_index" not in preview_text
        owner.get_by_role("button", name="Back to editing").click()

        owner.get_by_role("button", name="Save draft").click()
        save_message = owner.locator(".library-message")
        try:
            expect(save_message).to_contain_text("Draft created and saved", timeout=8000)
        except AssertionError as error:
            raise AssertionError(f"Draft save failed; sanitized API errors: {owner_api_errors}") from error
        quiz_id = owner.evaluate("sessionStorage.getItem('qv-unsaved-quiz')")
        # The unsaved record contains the API ID after a successful create; no bearer is in it.
        import json

        saved_draft = json.loads(quiz_id)
        public_quiz_id = saved_draft["id"]
        assert owner_key not in quiz_id
        assert all(owner_key not in url for url, _authorization in owner_requests)
        assert any(authorization == f"Bearer {owner_key}" for _url, authorization in owner_requests)
        owner.locator(".question-editor").first.locator('[data-question-field="prompt"]').fill(
            "Revised: Which star is closest to Earth?"
        )
        owner.get_by_role("button", name="Save draft").click()
        expect(owner.locator(".library-message")).to_contain_text("Changes saved")
        persisted_edit = owner_context.request.get(
            f"{origin}/api/quizzes/{public_quiz_id}",
            headers={"Authorization": f"Bearer {owner_key}"},
        )
        assert persisted_edit.status == 200
        assert persisted_edit.json()["questions"][0]["prompt"] == "Revised: Which star is closest to Earth?"
        assert persisted_edit.json()["questions"][1]["prompt"].startswith("First:")
        owner.get_by_role("button", name="Publish quiz").click()
        try:
            expect(owner.locator(".quiz-status.published")).to_be_visible()
        except AssertionError as error:
            raise AssertionError(f"Publish failed; sanitized API errors: {owner_api_errors}") from error

        # A malformed authoring request reaches the actual API and receives a safe 422.
        invalid_response = owner_context.request.post(
            f"{origin}/api/quizzes",
            headers={"Authorization": f"Bearer {owner_key}"},
            data={
                "title": "Invalid browser quiz",
                "category": "science",
                "questions": [{
                    "prompt": "Duplicate options?",
                    "options": ["Same", "Same", "Third", "Fourth"],
                    "correct_option_index": 0,
                }],
            },
        )
        assert invalid_response.status == 422
        assert owner_key not in invalid_response.text()

        guest.goto(origin, wait_until="domcontentloaded")
        guest.locator('[data-view="library"]').click()
        guest.locator('#quiz-search-form [name="q"]').fill("Stage Four Orbit Quiz")
        guest.get_by_role("button", name="Search quizzes").click()
        public_card = guest.locator(".library-card").filter(has_text="Stage Four Orbit Quiz")
        expect(public_card).to_be_visible()
        public_card.get_by_role("button", name="Preview quiz").click()
        expect(guest.locator(".preview-question").first.locator("h2")).to_have_text(
            "Revised: Which star is closest to Earth?"
        )
        assert "The Sun is our closest star." not in guest.locator(".preview-view").inner_text()
        assert all(owner_key not in url for url, _authorization in guest_requests)
        assert all(not authorization for url, authorization in guest_requests if "/api/quizzes" in url)
        assert_no_horizontal_overflow(guest)

        # Private quizzes remain owner-visible but absent from public browse/read paths.
        owner.locator('[data-quiz-action="back-library"]').click()
        owner.get_by_role("tab", name="My quizzes").click()
        owner.get_by_role("button", name="Create a quiz").click()
        owner.locator('[data-quiz-field="title"]').fill("Private Studio Draft")
        owner.locator('[data-quiz-field="category"]').fill("general")
        owner.locator('[data-quiz-action="add-question"]').click()
        fill_question(
            owner.locator(".question-editor").first,
            "A private question?",
            ["One", "Two", "Three", "Four"],
            2,
            "Private explanation.",
        )
        owner.get_by_role("button", name="Save draft").click()
        expect(owner.locator(".library-message")).to_contain_text("Draft created and saved")
        import json as json_module

        private_record = json_module.loads(owner.evaluate("sessionStorage.getItem('qv-unsaved-quiz')"))
        private_quiz_id = private_record["id"]
        owner.get_by_role("button", name="Publish quiz").click()
        expect(owner.locator(".quiz-status.published")).to_be_visible()
        owner.locator('[data-quiz-action="back-library"]').click()
        assert guest.request.get(f"{origin}/api/quizzes/{private_quiz_id}").status == 404

        guest.locator('[data-quiz-action="back-library"]').click()
        guest.locator('#quiz-search-form [name="q"]').fill("Private Studio Draft")
        guest.get_by_role("button", name="Search quizzes").click()
        expect(guest.get_by_text("No public quizzes found")).to_be_visible()

        # Duplicate the eligible public quiz into a private draft and confirm attribution.
        owner.get_by_role("tab", name="Explore public").click()
        owner.locator('#quiz-search-form [name="q"]').fill("Stage Four Orbit Quiz")
        owner.get_by_role("button", name="Search quizzes").click()
        public_owner_card = owner.locator(".library-card").filter(has_text="Stage Four Orbit Quiz")
        expect(public_owner_card).to_be_visible()
        public_owner_card.get_by_role("button", name="Duplicate").click()
        expect(owner.locator('[data-quiz-field="visibility"]')).to_have_value("private")
        expect(owner.locator('[data-quiz-field="description"]')).to_contain_text("Duplicated from public quiz")
        duplicate_title = owner.locator('[data-quiz-field="title"]').input_value()
        owner.get_by_role("button", name="Save draft").click()
        expect(owner.locator(".library-message")).to_contain_text("Changes saved")
        duplicate_record = json_module.loads(owner.evaluate("sessionStorage.getItem('qv-unsaved-quiz')"))
        duplicate_id = duplicate_record["id"]

        owner.once("dialog", lambda dialog: dialog.accept())
        owner.get_by_role("button", name="Delete quiz").click()
        expect(owner.locator(".library-message")).to_contain_text("Quiz deleted")
        assert guest.request.get(f"{origin}/api/quizzes/{duplicate_id}").status == 404

        # Unpublish removes a public quiz from the public listing.
        owner.get_by_role("tab", name="My quizzes").click()
        assert not owner.locator(".library-card").filter(has_text=duplicate_title).count()
        original_card = owner.locator(".library-card").filter(has_text="Stage Four Orbit Quiz")
        original_card.get_by_role("button", name="Edit quiz").click()
        owner.get_by_role("button", name="Unpublish").click()
        expect(owner.locator(".quiz-status")).to_have_text("Draft")
        guest.locator('#quiz-search-form [name="q"]').fill("Stage Four Orbit Quiz")
        guest.get_by_role("button", name="Search quizzes").click()
        expect(guest.get_by_text("No public quizzes found")).to_be_visible()
        owner.get_by_role("button", name="Publish quiz").click()
        expect(owner.locator(".quiz-status.published")).to_be_visible()

        # Stage 6: topic generation, review/edit/preview, draft save and reload.
        owner.locator('[data-quiz-action="back-library"]').click()
        owner.get_by_role("button", name="Create a quiz").click()
        expect(owner.get_by_role("heading", name="Build a first draft")).to_be_visible()
        topic_form = owner.locator("#ai-generator-form")
        expect(topic_form.locator("#ai-method")).to_have_value("topic")
        topic_form.locator('[name="subject"]').fill("Computer Science")
        topic_form.locator('[name="topic"]').fill("Operating Systems")
        topic_form.locator('[name="question_count"]').fill("2")
        topic_form.get_by_role("button", name="Generate questions").click()
        expect(owner.locator(".question-editor")).to_have_count(2)
        owner.locator('[data-quiz-action="preview-editor"]').click()
        expect(owner.locator(".preview-question").first.locator("h2")).to_have_text("Mock generated question 1?")
        assert "Option B is correct" not in owner.locator(".preview-view").inner_text()
        owner.get_by_role("button", name="Back to editing").click()
        owner.locator(".question-editor").first.locator('[data-question-field="prompt"]').fill("Edited mock question?")
        owner.get_by_role("button", name="Save draft").click()
        expect(owner.locator(".library-message")).to_contain_text("Draft created and saved")
        import json as json_module
        topic_record = json_module.loads(owner.evaluate("sessionStorage.getItem('qv-unsaved-quiz')"))
        topic_quiz_id = topic_record["id"]
        assert topic_record["visibility"] == "private"
        topic_authoring = owner_context.request.get(f"{origin}/api/quizzes/{topic_quiz_id}", headers={"Authorization": f"Bearer {owner_key}"})
        assert topic_authoring.status == 200
        assert topic_authoring.json()["provenance"] == "ai_assisted"
        owner.reload(wait_until="domcontentloaded")
        owner.locator('[data-view="library"]').click()
        owner.get_by_role("tab", name="My quizzes").click()
        topic_card = owner.locator(".library-card").filter(has_text="Operating Systems quiz")
        expect(topic_card).to_be_visible()
        topic_card.get_by_role("button", name="Edit quiz").click()
        expect(owner.locator(".question-editor").first.locator('[data-question-field="prompt"]')).to_have_value("Edited mock question?")

        # Stage 6 document flow uses the same deterministic provider.
        owner.locator('[data-quiz-action="back-library"]').click()
        owner.get_by_role("button", name="Create a quiz").click()
        document_form = owner.locator("#ai-generator-form")
        document_form.locator("#ai-method").select_option("document")
        document_form.locator('[name="document"]').set_input_files({"name": "course.txt", "mimeType": "text/plain", "buffer": b"Operating systems schedule processes and allocate memory."})
        document_form.locator('[name="question_count"]').fill("1")
        document_form.get_by_role("button", name="Generate questions").click()
        expect(owner.locator(".question-editor")).to_have_count(1)
        expect(owner.locator(".library-message")).to_contain_text("Generated questions are ready")
        owner.get_by_role("button", name="Save draft").click()
        expect(owner.locator(".library-message")).to_contain_text("Draft created and saved")
        document_record = json_module.loads(owner.evaluate("sessionStorage.getItem('qv-unsaved-quiz')"))
        assert document_record["visibility"] == "private"
        owner.reload(wait_until="domcontentloaded")
        owner.locator('[data-view="library"]').click()
        owner.get_by_role("tab", name="My quizzes").click()
        expect(owner.locator(".library-card").filter(has_text="Uploaded document quiz")).to_be_visible()

        # The UI navigation leaves two independent live room sessions connected and intact.
        owner.locator('[data-view="play"]').click()
        owner.locator("#player-name").fill("Library Host")
        owner.locator('[data-action="create"]').click()
        expect(owner.locator(".room-code")).to_be_visible()
        room_code = owner.locator(".room-code").inner_text()
        guest.locator('[data-view="play"]').click()
        guest.locator("#player-name").fill("Library Guest")
        guest.locator('[data-action="join"]').click()
        guest.locator("#room-code").fill(room_code)
        guest.locator('[data-action="confirm-join"]').click()
        expect(owner.locator(".player-row")).to_have_count(2)
        expect(guest.locator(".player-row")).to_have_count(2)
        owner.locator('[data-action="start"]').click()
        expect(owner.locator(".answer-button").first).to_be_visible(timeout=6000)
        round_before = owner.locator(".round-tag").inner_text()
        owner.locator('[data-view="library"]').click()
        expect(owner.get_by_role("heading", name="Find your next great question.")).to_be_visible()
        expect(owner.locator("#connection-status")).to_contain_text("ARENA LINK LIVE")
        owner.locator('[data-view="play"]').click()
        expect(owner.locator(".round-tag")).to_have_text(round_before)
        expect(guest.locator(".answer-button").first).to_be_visible()

        # Tablet and reduced-motion checks use the public library view.
        tablet.goto(origin, wait_until="domcontentloaded")
        tablet.locator('[data-view="library"]').click()
        assert_no_horizontal_overflow(tablet)
        tablet.emulate_media(reduced_motion="reduce")
        expect(tablet.locator(".library-card").first).to_be_visible()
        transition_property = tablet.locator(".library-card").first.evaluate(
            "element => getComputedStyle(element).transitionProperty"
        )
        assert transition_property == "none", f"Reduced-motion transition property was {transition_property!r}."

        print("Quiz Library browser verification passed: owner setup, CRUD/edit/reorder/preview, AI topic and document generation with review/edit/draft save/reload, validation, publish/unpublish, public/private visibility, duplication, delete, desktop/tablet/mobile overflow, keyboard focus, reduced motion, and live-room navigation continuity.")


def fill_question(card, prompt: str, options: list[str], correct: int, explanation: str) -> None:
    card.locator('[data-question-field="prompt"]').fill(prompt)
    for index, option in enumerate(options):
        card.locator(f'[data-option-index="{index}"]').fill(option)
    card.locator('[data-question-field="correct_option_index"]').select_option(str(correct))
    card.locator('[data-question-field="explanation"]').fill(explanation)


def capture_api_error(response, errors: list[tuple[int, object]]) -> None:
    if "/api/" not in response.url or response.status < 400:
        return
    try:
        errors.append((response.status, response.json()))
    except Exception:
        errors.append((response.status, None))


def assert_no_horizontal_overflow(page: Page) -> None:
    overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
    assert not overflow, f"Horizontal overflow at viewport {page.viewport_size}."


if __name__ == "__main__":
    main()
