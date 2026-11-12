"""Two independent Microsoft Edge sessions for end-to-end game verification."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, expect, sync_playwright

from quizverse.questions import QUESTIONS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EDGE_PATH = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


def main() -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "quizverse.app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=PROJECT_ROOT,
        env={**os.environ, "ALLOWED_ORIGINS": origin},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Uvicorn exited with code {process.returncode}")
            try:
                import urllib.request

                with urllib.request.urlopen(f"{origin}/health", timeout=0.3) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.05)
        else:
            raise RuntimeError("The FastAPI server did not start.")
        run_browser_sessions(origin)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def run_browser_sessions(origin: str) -> None:
    with sync_playwright() as playwright:
        if EDGE_PATH.exists():
            browser = playwright.chromium.launch(channel="msedge", headless=True)
        else:
            browser = playwright.chromium.launch(headless=True)
        desktop: BrowserContext = browser.new_context(viewport={"width": 1365, "height": 900})
        mobile: BrowserContext = browser.new_context(
            viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True
        )
        tablet_context: BrowserContext = browser.new_context(viewport={"width": 820, "height": 1024})
        host = desktop.new_page()
        guest = mobile.new_page()
        tablet = tablet_context.new_page()
        host.set_default_timeout(6000)
        guest.set_default_timeout(6000)
        tablet.set_default_timeout(6000)
        host_states: list[dict] = []
        guest_states: list[dict] = []
        tablet_states: list[dict] = []
        host_tokens: list[str] = []
        for page, states in ((host, host_states), (guest, guest_states), (tablet, tablet_states)):
            page.on("websocket", lambda ws, target=states, tokens=host_tokens if states is host_states else None: ws.on("framereceived", lambda payload: capture_state(target, payload, tokens)))

        host.goto(origin, wait_until="domcontentloaded")
        guest.goto(origin, wait_until="domcontentloaded")
        tablet.goto(origin, wait_until="domcontentloaded")
        expect(host.locator('[data-action="create"]')).to_be_visible()
        expect(host.locator("#player-preset")).to_have_value("teen_challenge")
        expect(host.locator("#player-difficulty")).to_have_value("medium")
        expect(host.locator("#player-language")).to_have_value("en")
        host.evaluate("localStorage.setItem('qv-player-preferences', '{corrupted')")
        host.reload(wait_until="domcontentloaded")
        expect(host.locator("#player-preset")).to_have_value("teen_challenge")
        expect(host.locator("#player-difficulty")).to_have_value("medium")
        host.locator(".match-settings").first.locator("summary").click()
        host.locator("#player-preset").select_option("expert_arena")
        host.locator("#player-difficulty").select_option("easy")
        host.locator("#player-language").fill("hi")
        host.locator("#player-language").dispatch_event("change")
        expect(host.locator("[data-topic-id]").first).to_be_visible(timeout=5000)
        host.locator("[data-topic-id]").first.select_option("increase")
        host.reload(wait_until="domcontentloaded")
        host.locator(".match-settings").first.locator("summary").click()
        expect(host.locator("#player-preset")).to_have_value("expert_arena")
        expect(host.locator("#player-difficulty")).to_have_value("easy")
        expect(host.locator("#player-language")).to_have_value("hi")
        expect(host.locator("[data-topic-id]").first).to_have_value("increase")
        host.locator("#player-preset").select_option("teen_challenge")
        host.locator("#player-difficulty").select_option("medium")
        expect(guest.locator('[data-action="join"]')).to_be_visible()
        assert_no_horizontal_overflow(guest)
        assert_no_horizontal_overflow(tablet)

        host.locator("#player-name").fill("Nova")
        host.locator('[data-action="create"]').click()
        expect(host.locator(".room-code")).to_be_visible()
        code = host.locator(".room-code").inner_text()
        expect(host.locator(".player-row")).to_have_count(1)

        guest.locator("#player-name").fill("Orbit")
        guest.locator('[data-action="join"]').click()
        guest.locator("#room-code").fill("XXXXXX")
        guest.locator('[data-action="confirm-join"]').click()
        expect(guest.locator("#toast")).to_contain_text("Room not found", timeout=5000)
        guest.locator("#room-code").fill(code)
        guest.locator('[data-action="confirm-join"]').click()
        expect(guest.locator(".player-row")).to_have_count(2)
        tablet.locator("#player-name").fill("Comet")
        tablet.locator('[data-action="join"]').click()
        tablet.locator("#room-code").fill(code)
        tablet.locator('[data-action="confirm-join"]').click()
        expect(tablet.locator(".player-row")).to_have_count(3)
        expect(guest.locator(".player-row")).to_have_count(3)
        expect(host.locator(".player-row")).to_have_count(3)
        assert_no_horizontal_overflow(guest)
        assert_no_horizontal_overflow(tablet)
        print("PASS: three independent desktop, tablet, and mobile browser sessions joined; invalid code rejected")

        # Close the host's real server-side WebSocket. The live page reconnects on its
        # own using its session token, while the other browser observes host transfer.
        host.close()
        host_player = guest.locator(".player-row").filter(has_text="Nova")
        guest_player = guest.locator(".player-row").filter(has_text="Orbit")
        expect(host_player.locator(".player-state")).to_contain_text("Reconnecting", timeout=8000)
        expect(guest_player.locator(".host-badge")).to_be_visible(timeout=8000)
        assert host_tokens, "The server did not provide the browser's guest-session token."
        host = desktop.new_page()
        host.set_default_timeout(6000)
        token_json = json.dumps(host_tokens[0])
        host.add_init_script(f"sessionStorage.setItem('qv-token', {token_json}); sessionStorage.setItem('qv-name', 'Nova');")
        host.on("websocket", lambda ws: ws.on("framereceived", lambda payload: capture_state(host_states, payload)))
        host.goto(origin, wait_until="domcontentloaded")
        expect(host.locator(".connection-indicator")).to_contain_text("ARENA LINK LIVE", timeout=10000)
        expect(host.locator(".player-row")).to_have_count(3)
        expect(guest.locator('[data-action="start"]')).to_be_visible(timeout=5000)
        print("PASS: host disconnect transferred control; the original browser reconnected with its guest token")

        guest.locator('[data-action="start"]').click()
        expect(guest.locator(".timer-ring")).to_be_visible()
        expect(guest.locator("#timer-number")).to_have_text("3", timeout=2500)
        expect(guest.locator(".answer-button").first).to_be_visible(timeout=6000)
        expect(host.locator(".answer-button").first).to_be_visible(timeout=6000)
        expect(tablet.locator(".answer-button").first).to_be_visible(timeout=6000)
        assert host.locator(".question-text").inner_text() == guest.locator(".question-text").inner_text()
        assert tablet.locator(".question-text").inner_text() == guest.locator(".question-text").inner_text()
        assert_no_horizontal_overflow(guest)
        assert_no_horizontal_overflow(tablet)
        question_states_host = [state for state in host_states if state.get("phase") == "question"]
        question_states_guest = [state for state in guest_states if state.get("phase") == "question"]
        assert question_states_host and question_states_guest
        first_deadline = question_states_host[0]["deadline"]
        assert first_deadline == question_states_guest[0]["deadline"]
        assert abs(first_deadline - int(time.time() * 1000) - 15000) < 3000

        answer_round(host, guest, tablet)
        expect(host.locator(".reveal-banner")).to_be_visible(timeout=5000)
        expect(guest.locator(".reveal-banner")).to_be_visible(timeout=5000)
        expect(tablet.locator(".reveal-banner")).to_be_visible(timeout=5000)
        first_scores = [int(re.sub(r"\D", "", value)) for value in guest.locator(".leaderboard-card .leader-score").all_text_contents()]
        assert max(first_scores) >= 1000
        print("PASS: both browsers received the same 15-second question deadline and simultaneous answers scored")

        previous = guest.locator(".question-text").inner_text()
        wait_for_next_question(guest, previous)
        wait_for_next_question(host, previous)
        wait_for_next_question(tablet, previous)
        timeout_question = guest.locator(".question-text").inner_text()
        guest.wait_for_timeout(15_500)
        expect(guest.locator(".reveal-banner")).to_be_visible(timeout=5000)
        expect(host.locator(".reveal-banner")).to_be_visible(timeout=5000)
        expect(tablet.locator(".reveal-banner")).to_be_visible(timeout=5000)
        print("PASS: unanswered round expired on the server and revealed to both browsers")

        previous = timeout_question
        for _ in range(8):
            wait_for_next_question(guest, previous)
            wait_for_next_question(host, previous)
            wait_for_next_question(tablet, previous)
            previous = guest.locator(".question-text").inner_text()
            answer_round(host, guest, tablet)
            expect(guest.locator(".reveal-banner")).to_be_visible(timeout=5000)
        expect(guest.locator(".results-card h2")).to_contain_text("takes the crown", timeout=6000)
        expect(host.locator(".results-card h2")).to_contain_text("takes the crown", timeout=6000)
        expect(tablet.locator(".results-card h2")).to_contain_text("takes the crown", timeout=6000)
        expect(guest.locator(".final-row")).to_have_count(3)
        print("PASS: full ten-question match reached final rankings in all three browser sessions")

        guest.locator('[data-action="rematch"]').click()
        expect(guest.locator(".room-code")).to_be_visible(timeout=3000)
        expect(host.locator(".room-code")).to_be_visible(timeout=3000)
        expect(tablet.locator(".room-code")).to_be_visible(timeout=3000)
        guest.locator('[data-action="start"]').click()
        expect(guest.locator(".answer-button").first).to_be_visible(timeout=6000)
        previous = guest.locator(".question-text").inner_text()
        for index in range(10):
            if index:
                wait_for_next_question(guest, previous)
                wait_for_next_question(host, previous)
                wait_for_next_question(tablet, previous)
                previous = guest.locator(".question-text").inner_text()
            answer_round(host, guest, tablet)
            expect(guest.locator(".reveal-banner")).to_be_visible(timeout=5000)
        expect(guest.locator(".results-card h2")).to_contain_text("takes the crown", timeout=6000)
        guest.locator('[data-action="lobby"]').click()
        expect(guest.locator(".room-code")).to_be_visible(timeout=3000)
        expect(host.locator(".room-code")).to_be_visible(timeout=3000)
        expect(tablet.locator(".room-code")).to_be_visible(timeout=3000)
        assert_no_horizontal_overflow(guest)
        assert_no_horizontal_overflow(tablet)
        print("PASS: rematch and return-to-lobby worked; mobile and tablet layouts had no horizontal overflow")

        run_speed_challenge(browser, origin)
        run_team_battle(browser, origin)
        run_elimination(browser, origin)
        run_solo_practice(browser, origin)
        run_quiz_royale(browser, origin)
        run_power_play(browser, origin)

        desktop.close()
        mobile.close()
        tablet_context.close()
        browser.close()


def run_speed_challenge(browser, origin: str) -> None:
    """Complete a Speed Challenge match using two isolated browser contexts."""
    host_context = browser.new_context(viewport={"width": 1280, "height": 850})
    guest_context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    host = host_context.new_page()
    guest = guest_context.new_page()
    host.set_default_timeout(7000)
    guest.set_default_timeout(7000)
    host_states: list[dict] = []
    guest_states: list[dict] = []
    for page, states in ((host, host_states), (guest, guest_states)):
        page.on("websocket", lambda ws, target=states: ws.on("framereceived", lambda payload: capture_state(target, payload)))
    try:
        host.goto(origin, wait_until="domcontentloaded")
        guest.goto(origin, wait_until="domcontentloaded")
        host.locator("#player-name").fill("Velocity")
        host.locator("#game-mode").select_option("speed_challenge")
        expect(host.locator("#mode-description")).to_contain_text("Momentum Shift is off")
        expect(host.locator(".format-row .format-pill:last-child")).to_contain_text("10s")
        host.locator('[data-action="create"]').click()
        expect(host.locator(".room-code")).to_be_visible()
        code = host.locator(".room-code").inner_text()
        guest.locator("#player-name").fill("Flash")
        guest.locator('[data-action="join"]').click()
        guest.locator("#room-code").fill(code)
        guest.locator('[data-action="confirm-join"]').click()
        expect(host.locator(".player-row")).to_have_count(2)
        expect(guest.locator(".player-row")).to_have_count(2)
        host.locator('[data-action="start"]').click()
        expect(host.locator(".answer-button").first).to_be_visible(timeout=7000)
        expect(guest.locator(".answer-button").first).to_be_visible(timeout=7000)
        assert host.locator(".question-text").inner_text() == guest.locator(".question-text").inner_text()
        host_question = next(state for state in reversed(host_states) if state.get("phase") == "question")
        guest_question = next(state for state in reversed(guest_states) if state.get("phase") == "question")
        assert host_question["mode"] == guest_question["mode"] == "speed_challenge"
        assert host_question["question_seconds"] == guest_question["question_seconds"] == 10
        assert host_question["deadline"] == guest_question["deadline"]
        remaining_ms = host_question["deadline"] - host_question["server_now"]
        assert 0 < remaining_ms <= 10000
        assert host_question["momentum"]["enabled"] is False
        assert host.locator(".momentum-note").count() == 1
        assert host.locator(".momentum-note").evaluate("node => node.hidden")
        assert_no_horizontal_overflow(guest)

        previous = host.locator(".question-text").inner_text()
        for index in range(10):
            if index:
                wait_for_next_question(host, previous)
                wait_for_next_question(guest, previous)
                previous = host.locator(".question-text").inner_text()
            answer_round(host, guest)
            expect(host.locator(".reveal-banner")).to_be_visible(timeout=5000)
            expect(guest.locator(".reveal-banner")).to_be_visible(timeout=5000)
        expect(host.locator(".results-card h2")).to_contain_text("takes the crown", timeout=7000)
        expect(guest.locator(".final-row")).to_have_count(2)
        expect(host.locator(".momentum-log")).to_contain_text("disabled in Speed Challenge")
        assert all("Momentum" not in text for text in host.locator(".final-stat").all_text_contents())
        assert_no_horizontal_overflow(guest)
        print("PASS: two independent desktop/mobile sessions completed all ten Speed Challenge rounds with a synchronized 10-second timer and Momentum Shift disabled")
    finally:
        host_context.close()
        guest_context.close()


def run_team_battle(browser, origin: str) -> None:
    """Complete Team Battle with four independent real browser clients."""
    contexts = [
        browser.new_context(viewport={"width": 1280, "height": 850}),
        browser.new_context(viewport={"width": 820, "height": 1024}),
        browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True),
        browser.new_context(viewport={"width": 430, "height": 932}, is_mobile=True, has_touch=True),
    ]
    pages = [context.new_page() for context in contexts]
    for page in pages:
        page.set_default_timeout(7000)
    client_states: list[list[dict]] = [[] for _ in pages]
    for page, states in zip(pages, client_states):
        page.on("websocket", lambda ws, target=states: ws.on("framereceived", lambda payload: capture_state(target, payload)))
    host, *guests = pages
    try:
        for page in pages:
            page.goto(origin, wait_until="domcontentloaded")
        host.locator("#player-name").fill("Violet Captain")
        host.locator("#game-mode").select_option("team_battle")
        expect(host.locator("#mode-description")).to_contain_text("even roster")
        host.locator('[data-action="create"]').click()
        code = host.locator(".room-code").inner_text()
        for index, page in enumerate(guests[:2], start=1):
            page.locator("#player-name").fill(f"Battle {index}")
            page.locator('[data-action="join"]').click()
            page.locator("#room-code").fill(code)
            page.locator('[data-action="confirm-join"]').click()
            expect(page.locator(".player-row")).to_have_count(index + 1)
        expect(host.locator('[data-action="start"]')).to_be_disabled()
        expect(host.locator(".room-card .microcopy").last).to_contain_text("4, 6, or 8")
        fourth_player = guests[2]
        fourth_player.locator("#player-name").fill("Battle Four")
        fourth_player.locator('[data-action="join"]').click()
        fourth_player.locator("#room-code").fill(code)
        fourth_player.locator('[data-action="confirm-join"]').click()
        expect(host.locator("[data-action='start']")).to_be_enabled()
        all_pages = pages
        all_states = client_states
        for page in all_pages:
            expect(page.locator(".player-row")).to_have_count(4)
            badges = page.locator(".player-row .team-pill").all_text_contents()
            assert badges.count("Team Violet") == 2
            assert badges.count("Team Cyan") == 2
        host.locator('[data-action="start"]').click()
        expect(host.locator(".answer-button").first).to_be_visible(timeout=7000)
        for page in all_pages[1:]:
            expect(page.locator(".answer-button").first).to_be_visible(timeout=7000)
        questions = [
            next(state for state in reversed(states) if state.get("phase") == "question")
            for states in all_states
        ]
        first = questions[0]
        for question in questions[1:]:
            assert question["deadline"] == first["deadline"]
            assert question["teams"] == first["teams"]
        assert first["question_seconds"] == 15
        assert 0 < first["deadline"] - first["server_now"] <= 15000
        assert first["momentum"]["enabled"] is False
        for page in all_pages:
            assert page.locator(".momentum-note").evaluate("node => node.hidden")
            assert_no_horizontal_overflow(page)

        previous = host.locator(".question-text").inner_text()
        for index in range(10):
            if index:
                for page in all_pages:
                    wait_for_next_question(page, previous)
                previous = host.locator(".question-text").inner_text()
            answer_round(host, guests[0], guests[1], guests[2])
            for page in all_pages:
                expect(page.locator(".reveal-banner")).to_be_visible(timeout=5000)
                expect(page.locator(".team-live-scoreboard")).to_be_visible()
        expect(host.locator(".team-final-results")).to_be_visible()
        expect(host.locator(".team-result-card")).to_have_count(2)
        expect(host.locator(".final-row")).to_have_count(4)
        result_state = next(state for state in reversed(client_states[0]) if state.get("phase") == "results")
        team_results = result_state["results"]["teams"]
        assert len(result_state["results"]["winning_team_ids"]) in {1, 2}
        assert result_state["results"]["is_draw"] is (len(result_state["results"]["winning_team_ids"]) == 2)
        for team in team_results:
            member_score = sum(player["score"] for player in result_state["players"] if player["team_id"] == team["team_id"])
            member_correct = sum(player["correct"] for player in result_state["players"] if player["team_id"] == team["team_id"])
            member_time = sum(player["correct_response_time_ms"] for player in result_state["players"] if player["team_id"] == team["team_id"])
            assert (team["score"], team["correct"], team["correct_response_time_ms"]) == (member_score, member_correct, member_time)
        assert_no_horizontal_overflow(guests[1])
        assert_no_horizontal_overflow(guests[2])
        print("PASS: four independent browser clients completed Team Battle with alternating teams, synchronized 15-second deadlines, individual contributions, aggregate standings, and final placement")
    finally:
        for context in contexts:
            context.close()


def run_elimination(browser, origin: str) -> None:
    """Verify elimination with three independent clients and a server timeout."""
    contexts = [
        browser.new_context(viewport={"width": 1280, "height": 850}),
        browser.new_context(viewport={"width": 820, "height": 1024}),
        browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True),
    ]
    pages = [context.new_page() for context in contexts]
    states: list[list[dict]] = [[] for _ in pages]
    host, wrong_player, timeout_player = pages
    try:
        for page, captured in zip(pages, states):
            page.set_default_timeout(7000)
            page.on("websocket", lambda ws, target=captured: ws.on("framereceived", lambda payload: capture_state(target, payload)))
            page.goto(origin, wait_until="domcontentloaded")
        host.locator("#player-name").fill("Last Light")
        host.locator("#game-mode").select_option("elimination")
        expect(host.locator("#mode-description")).to_contain_text("wrong or unanswered")
        host.locator('[data-action="create"]').click()
        code = host.locator(".room-code").inner_text()
        for page, name in ((wrong_player, "Wrong Turn"), (timeout_player, "Final Bell")):
            page.locator("#player-name").fill(name)
            page.locator('[data-action="join"]').click()
            page.locator("#room-code").fill(code)
            page.locator('[data-action="confirm-join"]').click()
        expect(host.locator(".player-row")).to_have_count(3)
        expect(host.locator('[data-action="start"]')).to_be_enabled()
        host.locator('[data-action="start"]').click()
        for page in pages:
            expect(page.locator(".answer-button").first).to_be_visible(timeout=7000)
            assert_no_horizontal_overflow(page)
        question_states = [next(item for item in reversed(captured) if item.get("phase") == "question") for captured in states]
        assert all(item["mode"] == "elimination" and item["question_seconds"] == 15 for item in question_states)
        assert len({item["deadline"] for item in question_states}) == 1
        assert all(item["momentum"]["enabled"] is False for item in question_states)

        prompt = host.locator(".question-text").inner_text()
        correct = next(question.answer for question in QUESTIONS if question.prompt == prompt)
        host.locator(".answer-button").nth(correct).click()
        wrong_player.locator(".answer-button").nth((correct + 1) % 4).click()
        timeout_player.locator(".answer-button").nth(correct).click()
        for page in pages:
            expect(page.locator(".reveal-banner")).to_be_visible(timeout=5000)
        expect(host.locator(".remaining-count")).to_contain_text("2 still in")
        wrong_status = states[1][-1]
        assert next(player for player in wrong_status["players"] if player["name"] == "Wrong Turn")["elimination_reason"] == "incorrect"

        previous = host.locator(".question-text").inner_text()
        wait_for_next_question(host, previous)
        wait_for_next_question(timeout_player, previous)
        expect(wrong_player.locator(".eliminated-watching")).to_be_visible()
        expect(wrong_player.locator(".answer-button")).to_have_count(0)
        second_prompt = host.locator(".question-text").inner_text()
        second_correct = next(question.answer for question in QUESTIONS if question.prompt == second_prompt)
        host.locator(".answer-button").nth(second_correct).click()
        deadline = next(item["deadline"] for item in reversed(states[0]) if item.get("phase") == "question")
        wait_ms = max(0, deadline - int(time.time() * 1000) + 350)
        host.wait_for_timeout(wait_ms)
        for page in pages:
            expect(page.locator(".results-card")).to_be_visible(timeout=10000)
        final = next(item for item in reversed(states[0]) if item.get("phase") == "results")
        assert final["results"]["end_reason"] == "one_survivor"
        assert final["results"]["winning_player_ids"] == [final["players"][0]["id"]]
        assert final["results"]["leaderboard"][0]["name"] == "Last Light"
        assert next(player for player in final["results"]["leaderboard"] if player["name"] == "Wrong Turn")["eliminated_round"] == 1
        assert next(player for player in final["results"]["leaderboard"] if player["name"] == "Final Bell")["elimination_reason"] == "timeout"
        expect(wrong_player.locator(".final-row")).to_have_count(3)
        expect(host.locator(".results-copy")).to_contain_text("Last player standing")
        assert_no_horizontal_overflow(timeout_player)
        print("PASS: three independent desktop/tablet/mobile clients verified Elimination selection, synchronized 15-second deadline, wrong-answer and timeout elimination, spectator view, last-survivor ranking, and responsive layout")
    finally:
        for context in contexts:
            context.close()


def run_solo_practice(browser, origin: str) -> None:
    """Complete two solo matches in one independent browser context."""
    context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = context.new_page()
    page.set_default_timeout(7000)
    states: list[dict] = []
    page.on("websocket", lambda ws: ws.on("framereceived", lambda payload: capture_state(states, payload)))
    try:
        page.goto(origin, wait_until="domcontentloaded")
        mode = page.locator("#game-mode")
        mode.focus()
        page.keyboard.press("End")
        page.keyboard.press("Enter")
        expect(mode).to_have_value("solo_practice")
        expect(page.locator("#mode-description")).to_contain_text("one real player")
        focus_outline = mode.evaluate("node => getComputedStyle(node).outlineStyle")
        assert focus_outline != "none"
        page.locator("#player-name").fill("Solo Nova")
        expect(page.locator('[data-action="create"]')).to_contain_text("Create Solo Practice")
        page.locator('[data-action="create"]').click()
        expect(page.locator(".room-code-row")).to_be_hidden()
        expect(page.locator(".player-list")).to_be_hidden()
        expect(page.locator("[data-action='start']")).to_be_enabled()
        expect(page.locator("[data-action='start']")).to_contain_text("Start Practice")
        expect(page.locator(".room-note")).to_contain_text("Nothing to share")
        code = next(item["code"] for item in reversed(states) if item.get("mode") == "solo_practice")
        player_id = next(player["id"] for player in states[-1]["players"] if player["name"] == "Solo Nova")
        page.locator('[data-action="start"]').click()
        expect(page.locator(".answer-button").first).to_be_visible(timeout=7000)
        expect(page.locator(".timer-ring")).to_be_visible()
        expect(page.locator(".solo-score")).to_contain_text("SCORE")
        assert page.locator(".leaderboard-card").is_hidden()
        question_state = next(item for item in reversed(states) if item.get("phase") == "question")
        assert question_state["mode"] == "solo_practice"
        assert question_state["question_seconds"] == 15
        assert question_state["momentum"]["enabled"] is False
        assert "answer" not in question_state["question"]

        previous = ""
        for index in range(10):
            if index:
                wait_for_next_question(page, previous)
            prompt = page.locator(".question-text").inner_text()
            correct = next(question.answer for question in QUESTIONS if question.prompt == prompt)
            previous = prompt
            if index == 2:
                deadline = next(item["deadline"] for item in reversed(states) if item.get("phase") == "question")
                wait_ms = max(0, deadline - int(time.time() * 1000) + 350)
                page.wait_for_timeout(wait_ms)
                expect(page.locator(".reveal-banner")).to_be_visible(timeout=7000)
                expect(page.locator(".answer-status")).to_contain_text("Your answer")
            else:
                selected = (correct + 1) % 4 if index == 1 else correct
                page.locator(".answer-button").nth(selected).click()
                expect(page.locator(".reveal-banner")).to_be_visible(timeout=5000)
                if index == 1:
                    expect(page.locator(".reveal-banner")).to_contain_text("right answer")
        expect(page.locator(".practice-result-grid")).to_be_visible(timeout=7000)
        expect(page.locator(".results-card h2")).to_have_text("Practice complete.")
        expect(page.locator(".practice-result-grid")).to_contain_text("Correct")
        summary = next(item["results"]["practice_summary"] for item in reversed(states) if item.get("phase") == "results")
        assert summary["correct_answers"] == 8
        assert summary["incorrect_answers"] == 1
        assert summary["unanswered_questions"] == 1
        assert summary["accuracy_percent"] == 88.9
        assert summary["score"] > 0
        assert page.locator(".podium").is_hidden()
        assert page.locator(".final-list").is_hidden()
        assert page.locator(".practice-result-grid").inner_text().find("88.9%") >= 0
        assert_no_horizontal_overflow(page)
        print("PASS: one independent mobile browser completed ten Solo Practice questions with correct, incorrect, and timed-out answers; personal score, counts, accuracy, timer, and answer feedback matched server snapshots")

        expect(page.locator("[data-action='rematch']")).to_have_text("Practice again")
        page.locator('[data-action="rematch"]').click()
        expect(page.locator(".answer-button").first).to_be_visible(timeout=7000)
        replay_question = next(item for item in reversed(states) if item.get("phase") == "question")
        assert replay_question["code"] == code
        assert replay_question["players"][0]["id"] == player_id
        assert replay_question["players"][0]["score"] == 0
        expect(page.locator(".solo-score")).to_contain_text("SCORE  0")
        previous = ""
        for index in range(10):
            if index:
                wait_for_next_question(page, previous)
            previous = page.locator(".question-text").inner_text()
            answer = next(question.answer for question in QUESTIONS if question.prompt == previous)
            page.locator(".answer-button").nth(answer).click()
            expect(page.locator(".reveal-banner")).to_be_visible(timeout=5000)
        expect(page.locator(".practice-result-grid")).to_be_visible(timeout=7000)
        page.locator('[data-action="lobby"]').click()
        expect(page.locator("[data-action='start']")).to_be_visible()
        lobby_state = next(item for item in reversed(states) if item.get("phase") == "waiting")
        assert lobby_state["code"] == code
        assert lobby_state["players"][0]["id"] == player_id
        assert lobby_state["players"][0]["score"] == 0
        print("PASS: Solo Practice replay started a complete second match with the same player identity; return-to-lobby reset the score")
    finally:
        context.close()


def run_quiz_royale(browser, origin: str) -> None:
    contexts = [browser.new_context(viewport={"width": size[0], "height": size[1]}, is_mobile=mobile, has_touch=mobile) for size, mobile in [((1280, 850), False), ((820, 1024), False), ((390, 844), True), ((430, 932), True)]]
    pages = [context.new_page() for context in contexts]
    states: list[list[dict]] = [[] for _ in pages]
    host, *guests = pages
    try:
        for page, captured in zip(pages, states):
            page.set_default_timeout(7000)
            page.on("websocket", lambda ws, target=captured: ws.on("framereceived", lambda payload: capture_state(target, payload)))
            page.goto(origin, wait_until="domcontentloaded")
        host.locator("#player-name").fill("Royale Host")
        host.locator("#game-mode").select_option("quiz_royale")
        expect(host.locator("#match-count")).to_have_value("15")
        host.locator('[data-action="create"]').click()
        code = host.locator(".room-code").inner_text()
        for index, page in enumerate(guests, start=1):
            page.locator("#player-name").fill(f"Royale {index}")
            page.locator('[data-action="join"]').click()
            page.locator("#room-code").fill(code)
            page.locator('[data-action="confirm-join"]').click()
        expect(host.locator(".player-row")).to_have_count(4)
        expect(host.locator(".capacity")).to_contain_text("4 / 20")
        expect(host.locator("[data-action='start']")).to_be_enabled()
        host.locator('[data-action="start"]').click()
        for page in pages:
            expect(page.locator(".answer-button").first).to_be_visible(timeout=7000)
            expect(page.locator(".remaining-count")).to_contain_text("4 still in")
            assert_no_horizontal_overflow(page)
        initial = [next(item for item in reversed(captured) if item.get("phase") == "question") for captured in states]
        assert all(item["mode"] == "quiz_royale" and item["question_seconds"] == 12 and item["total_rounds"] == 15 for item in initial)
        assert len({item["deadline"] for item in initial}) == 1
        assert all(player["lives"] == 3 for player in initial[0]["players"])

        # Reconnect while the same round is open and confirm both its question and deadline survive.
        prompt = host.locator(".question-text").inner_text()
        guest = guests[0]
        guest.reload(wait_until="domcontentloaded")
        expect(guest.locator(".answer-button").first).to_be_visible(timeout=7000)
        assert guest.locator(".question-text").inner_text() == prompt
        reconnected = next(item for item in reversed(states[1]) if item.get("phase") == "question")
        assert reconnected["deadline"] == initial[1]["deadline"]

        previous = ""
        for round_index in range(3):
            if round_index:
                for page in pages:
                    wait_for_next_question(page, previous)
                previous = host.locator(".question-text").inner_text()
            else:
                previous = prompt
            correct = next(question.answer for question in QUESTIONS if question.prompt == previous)
            host.locator(f'.answer-button[data-choice="{correct}"]').click()
            for page in guests:
                page.locator(f'.answer-button[data-choice="{(correct + 1) % 4}"]').click()
            for page in pages:
                expect(page.locator(".reveal-banner")).to_be_visible(timeout=5000)
        expect(host.locator(".results-card")).to_be_visible(timeout=6000)
        result = next(item for item in reversed(states[0]) if item.get("phase") == "results")
        assert result["results"]["end_reason"] == "one_survivor"
        assert result["remaining_players"] == 1
        assert len(result["results"]["leaderboard"]) == 4
        host.locator('[data-action="rematch"]').click()
        expect(host.locator("[data-action='start']")).to_be_enabled(timeout=5000)
        lobby = next(item for item in reversed(states[0]) if item.get("phase") == "waiting")
        assert all(player["lives"] == 3 for player in lobby["players"])
        host.locator('[data-action="start"]').click()
        expect(host.locator(".answer-button").first).to_be_visible(timeout=7000)
        assert_no_horizontal_overflow(guests[1])
        print("PASS: four desktop/tablet/mobile players completed Quiz Royale; verified 20-player capacity display, shared 12-second question, 3 lives, reconnect sequence continuity, last-survivor result, and rematch reset")
    finally:
        for context in contexts:
            context.close()


def run_power_play(browser, origin: str) -> None:
    host_context = browser.new_context(viewport={"width": 1280, "height": 850})
    guest_context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    host, guest = host_context.new_page(), guest_context.new_page()
    states: list[list[dict]] = [[], []]
    for page, captured in ((host, states[0]), (guest, states[1])):
        page.set_default_timeout(7000)
        page.on("websocket", lambda ws, target=captured: ws.on("framereceived", lambda payload: capture_state(target, payload)))
    try:
        host.goto(origin, wait_until="domcontentloaded")
        guest.goto(origin, wait_until="domcontentloaded")
        host.locator("#player-name").fill("Power Host")
        host.locator("#game-mode").select_option("power_play")
        host.locator('[data-action="create"]').click()
        code = host.locator(".room-code").inner_text()
        guest.locator("#player-name").fill("Power Guest")
        guest.locator('[data-action="join"]').click()
        guest.locator("#room-code").fill(code)
        guest.locator('[data-action="confirm-join"]').click()
        host.locator('[data-action="start"]').click()
        expect(host.locator(".answer-button").first).to_be_visible(timeout=7000)
        expect(host.locator(".ability-button")).to_have_count(3)
        expect(guest.locator(".ability-button")).to_have_count(3)
        first_question = host.locator(".question-text").inner_text()
        first_correct = next(question.answer for question in QUESTIONS if question.prompt == first_question)
        host.locator('[data-action="ability"][data-ability="fifty_fifty"]').click()
        expect(host.locator(".answer-button")).to_have_count(2)
        expect(guest.locator(".answer-button")).to_have_count(4)
        assert next(item for item in reversed(states[0]) if item.get("phase") == "question")["power_play"]["energy"] == 1
        assert next(item for item in reversed(states[1]) if item.get("phase") == "question")["power_play"]["energy"] == 3
        host.locator(f'.answer-button[data-choice="{first_correct}"]').click()
        guest.locator(f'.answer-button[data-choice="{first_correct}"]').click()
        expect(host.locator(".reveal-banner")).to_be_visible(timeout=5000)

        previous = first_question
        for round_index in range(1, 10):
            if round_index:
                wait_for_next_question(host, previous)
                wait_for_next_question(guest, previous)
                previous = host.locator(".question-text").inner_text()
            correct = next(question.answer for question in QUESTIONS if question.prompt == previous)
            if round_index == 1:
                host.locator('[data-action="ability"][data-ability="double_points"]').click()
                expect(host.locator(".power-play-controls")).to_contain_text("Double Points armed")
            elif round_index == 3:
                host.locator('[data-action="ability"][data-ability="shield"]').click()
                expect(host.locator(".power-play-controls")).to_contain_text("Shield active")
                host.locator(f'.answer-button[data-choice="{(correct + 1) % 4}"]').click()
                guest.locator(f'.answer-button[data-choice="{correct}"]').click()
                expect(host.locator(".reveal-banner")).to_be_visible(timeout=5000)
                feedback = next(item["answer_feedback"] for item in reversed(states[0]) if item.get("phase") == "reveal")
                assert feedback["shield_consumed"] is True and feedback["power_penalty"] is False
                previous = host.locator(".question-text").inner_text()
                continue
            host.locator(f'.answer-button[data-choice="{correct}"]').click()
            guest.locator(f'.answer-button[data-choice="{correct}"]').click()
            expect(host.locator(".reveal-banner")).to_be_visible(timeout=5000)
            if round_index == 1:
                feedback = next(item["answer_feedback"] for item in reversed(states[0]) if item.get("phase") == "reveal")
                assert feedback["doubled"] is True
        expect(host.locator(".results-card")).to_be_visible(timeout=7000)
        expect(guest.locator(".final-row")).to_have_count(2)
        assert_no_horizontal_overflow(guest)
        print("PASS: desktop/mobile Power Play match exercised private 50:50, Double Points scoring, Shield consumption, energy snapshots, and completed all ten rounds")
    finally:
        host_context.close()
        guest_context.close()


def capture_state(states: list[dict], payload: str | bytes, tokens: list[str] | None = None) -> None:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    try:
        message = json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        return
    if isinstance(message, dict) and isinstance(message.get("state"), dict):
        states.append(message["state"])
    if isinstance(message, dict) and message.get("event") == "joined" and isinstance(message.get("token"), str) and tokens is not None:
        tokens.append(message["token"])


def answer_round(first: Page, second: Page, *others: Page) -> None:
    prompt = first.locator(".question-text").inner_text()
    correct_choice = next(question.answer for question in QUESTIONS if question.prompt == prompt)
    first.locator(".answer-button").nth(correct_choice).click()
    wait_for_answer_count(second, 1)
    second.locator(".answer-button").nth(correct_choice).click()
    for answered, page in enumerate(others, start=2):
        wait_for_answer_count(page, answered)
        page.locator(".answer-button").nth(correct_choice).click()


def wait_for_answer_count(page: Page, expected: int) -> None:
    page.wait_for_function(
        "expected => { const status = document.querySelector('.answer-status'); if (!status) return false; const match = status.innerText.match(/(\\d+) \\//); return !!match && Number(match[1]) >= expected; }",
        arg=expected,
        timeout=5000,
    )
    # The server snapshot causes a client re-render; let its short card transition
    # settle before the next real browser click.
    page.wait_for_timeout(160)


def wait_for_next_question(page: Page, previous: str) -> None:
    page.wait_for_function(
        "previous => { const prompt = document.querySelector('.question-text'); return prompt && prompt.innerText !== previous && document.querySelector('.answer-button'); }",
        arg=previous,
        timeout=9000,
    )


def assert_no_horizontal_overflow(page: Page) -> None:
    dimensions = page.evaluate("({width: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth})")
    assert dimensions["scroll"] <= dimensions["width"], f"horizontal overflow: {dimensions}"


if __name__ == "__main__":
    main()
