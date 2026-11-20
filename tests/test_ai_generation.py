from __future__ import annotations

import json
import os
import re
import zipfile
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from quizverse.persistence.authorization import QuizAccessService
from quizverse.question_intelligence import SelectionFilters, select_questions
from quizverse.taxonomy import CATEGORIES

from quizverse.ai_generation import (
    GeneratedQuestion,
    GeneratedQuestions,
    GenerationError,
    GenerationLimiter,
    GenerationRequest,
    GenerationService,
    GeminiProvider,
    extract_upload,
)
from starlette.datastructures import UploadFile
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pathlib import Path
from quizverse.persistence.db import Database
from quizverse.realtime import create_app
from pypdf import PdfWriter


@pytest.fixture
def ai_client(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "ai-api.sqlite3"
    database_url = f"sqlite:///{db_path.as_posix()}"
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    try:
        command.upgrade(Config(str(project_root / "alembic.ini")), "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
    database = Database(database_url)
    try:
        with TestClient(create_app(database=database)) as client:
            yield client
    finally:
        database.close()


class MockProvider:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = 0

    async def generate(self, prompt, schema, *, max_output_tokens):
        self.calls += 1
        self.prompts = getattr(self, "prompts", []) + [prompt]
        if self.error:
            raise self.error
        if self.response is not None:
            return self.response
        count = int(re.search(r"Requested count: (\d+)", prompt).group(1))
        difficulty = re.search(r"Difficulty: (easy|medium|hard)", prompt).group(1)
        questions = []
        for index in range(count):
            questions.append({
                "prompt": f"Question number {index + 1}?",
                "options": [f"Choice {index + 1}A", f"Choice {index + 1}B", f"Choice {index + 1}C", f"Choice {index + 1}D"],
                "correct_option_index": 1,
                "explanation": "This is the correct explanation.",
                "difficulty": difficulty,
                "category": "science",
            })
        return json.dumps({"questions": questions})


def test_topic_generation_and_server_signed_ai_draft_save(ai_client):
    client = ai_client
    service = client.app.state.generation_service
    service.provider = MockProvider()
    owner = client.post("/api/quiz-owners").json()["capability"]
    headers = {"Authorization": f"Bearer {owner}"}
    response = client.post("/api/ai/generations", headers=headers, json={
        "method": "topic", "subject": "Computer Science", "topic": "Operating Systems",
        "category": "category:technology-computing-fundamentals",
        "objectives": "Understand scheduling", "question_count": 2, "difficulty": "medium",
    })
    assert response.status_code == 200
    generated = response.json()
    assert len(generated["questions"]) == 2
    assert "correct_option_index" in generated["questions"][0]
    assert service.provider.calls == 1

    payload = {
        "title": "OS Basics", "category": "technology-computing-fundamentals", "difficulty": "medium",
        "visibility": "public", "questions": [
            {**question, "time_limit_seconds": 15} for question in generated["questions"]
        ],
        "generation_ticket": generated["generation_ticket"],
    }
    saved = client.post("/api/quizzes", headers=headers, json=payload)
    assert saved.status_code == 201, saved.text
    assert all(question["category_id"] == "category:technology-computing-fundamentals" for question in saved.json()["questions"])
    assert client.get("/api/quizzes", params={"category": "technology-computing-fundamentals"}).status_code == 200
    assert saved.json()["status"] == "draft"
    assert saved.json()["provenance"] == "ai_assisted"
    assert all(question["provenance"] == "ai_assisted" for question in saved.json()["questions"])

    repeated = client.post("/api/ai/generations", headers=headers, json={
        "method": "topic", "subject": "Computer Science", "topic": "Operating Systems",
        "category": "category:technology-computing-fundamentals",
        "objectives": "Understand scheduling", "question_count": 2, "difficulty": "medium",
    })
    assert repeated.status_code == 502
    assert repeated.json()["error"]["code"] == "invalid_generated_output"
    assert service.provider.calls == 3  # Initial generation plus one bounded retry.

    update = client.patch(f"/api/quizzes/{saved.json()['id']}", headers=headers, json={
        "expected_version": saved.json()["version"], "title": "Reviewed OS Basics",
        "questions": [{**question, "prompt": "Reviewed: " + question["prompt"]} for question in payload["questions"]],
    })
    assert update.status_code == 200
    assert update.json()["provenance"] == "ai_assisted"
    stale = client.patch(f"/api/quizzes/{saved.json()['id']}", headers=headers, json={
        "expected_version": saved.json()["version"], "title": "Stale edit",
    })
    assert stale.status_code == 409

    public = client.post(f"/api/quizzes/{saved.json()['id']}/publish", headers=headers)
    assert public.status_code == 200
    player = client.get(f"/api/quizzes/{saved.json()['id']}").json()
    assert "correct_option_index" not in player["questions"][0]
    assert "explanation" not in player["questions"][0]
    access = QuizAccessService(service.database)
    game_inventory = access.game_question_source(
        quiz_ids=(saved.json()["id"],), capability=owner,
    )
    parent_by_id = {
        item.id: f"category:{item.parent_slug}" if item.parent_slug else None
        for item in CATEGORIES
    }
    selected = select_questions(game_inventory, SelectionFilters(
        count=2, difficulty="medium", categories=frozenset({"category:technology-computing-fundamentals"}),
        parent_by_id=parent_by_id,
    ))
    assert len(selected) == 2
    assert all(item.category_id == "category:technology-computing-fundamentals" for item in selected)



def test_generation_requires_owner_and_disabled_service_is_clear(ai_client):
    client = ai_client
    client.app.state.generation_service.provider = None
    request = {"subject": "Science", "topic": "Space", "question_count": 1, "difficulty": "easy"}
    assert client.post("/api/ai/generations", json=request).status_code == 401
    owner = client.post("/api/quiz-owners").json()["capability"]
    response = client.post("/api/ai/generations", headers={"Authorization": f"Bearer {owner}"}, json=request)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ai_generation_unavailable"


def test_document_api_generation_uses_owner_and_returns_unsaved_questions(ai_client):
    client = ai_client
    client.app.state.generation_service.provider = MockProvider()
    owner = client.post("/api/quiz-owners").json()["capability"]
    response = client.post(
        "/api/ai/generations/document",
        headers={"Authorization": f"Bearer {owner}"},
        data={"subject": "Document", "topic": "Scheduling", "question_count": "1", "difficulty": "medium"},
        files={"document": ("notes.txt", b"Scheduling selects which process receives CPU time.", "text/plain")},
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["questions"]) == 1
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("filename,mime,content", [
    ("notes.txt", "text/plain", b"Course notes about scheduling."),
    ("notes.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", None),
])
def test_document_extraction_txt_and_docx(filename, mime, content):
    if content is None:
        stream = BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", '<document xmlns:w="urn:w"><w:t>Document text</w:t></document>')
        content = stream.getvalue()
    upload = UploadFile(filename=filename, file=BytesIO(content), headers={"content-type": mime})
    extracted = extract_upload(upload, content)
    assert extracted.strip()


def test_document_extraction_rejects_wrong_mime_and_empty_text():
    with pytest.raises(GenerationError) as unsupported:
        extract_upload(UploadFile(filename="fake.pdf", file=BytesIO(b"hello"), headers={"content-type": "text/plain"}), b"hello")
    assert unsupported.value.code == "unsupported_document"
    with pytest.raises(GenerationError) as empty:
        extract_upload(UploadFile(filename="empty.txt", file=BytesIO(b"  "), headers={"content-type": "text/plain"}), b"  ")
    assert empty.value.code == "empty_document"

    with pytest.raises(GenerationError) as corrupted:
        extract_upload(UploadFile(filename="broken.docx", file=BytesIO(b"not a zip"), headers={"content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}), b"not a zip")
    assert corrupted.value.code == "invalid_document"
    with pytest.raises(GenerationError) as unsupported_extension:
        extract_upload(UploadFile(filename="notes.doc", file=BytesIO(b"data"), headers={"content-type": "application/msword"}), b"data")
    assert unsupported_extension.value.code == "unsupported_document"

def _text_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return bytes(output)


def test_pdf_extraction_encrypted_pdf_and_text_limit():
    pdf = _text_pdf("Operating systems schedule processes")
    extracted = extract_upload(UploadFile(filename="notes.pdf", file=BytesIO(pdf), headers={"content-type": "application/pdf"}), pdf)
    assert "schedule processes" in extracted

    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    writer.encrypt("secret")
    encrypted = BytesIO()
    writer.write(encrypted)
    encrypted_bytes = encrypted.getvalue()
    with pytest.raises(GenerationError) as encrypted_error:
        extract_upload(UploadFile(filename="secret.pdf", file=BytesIO(encrypted_bytes), headers={"content-type": "application/pdf"}), encrypted_bytes)
    assert encrypted_error.value.code == "encrypted_document"

    too_long = b"a" * 100_001
    with pytest.raises(GenerationError) as length_error:
        extract_upload(UploadFile(filename="long.txt", file=BytesIO(too_long), headers={"content-type": "text/plain"}), too_long)
    assert length_error.value.code == "extracted_text_too_large"
    too_large = b"a" * (10 * 1024 * 1024 + 1)
    with pytest.raises(GenerationError) as size_error:
        extract_upload(UploadFile(filename="large.txt", file=BytesIO(too_large), headers={"content-type": "text/plain"}), too_large)
    assert size_error.value.code == "upload_too_large"


def test_docx_zip_entry_and_expanded_size_limits():
    for kind in ("entries", "expanded"):
        stream = BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", "<document>Text</document>")
            if kind == "entries":
                for index in range(255):
                    archive.writestr(f"extra/{index}.xml", "x")
            else:
                archive.writestr("extra/large.xml", "x" * (20 * 1024 * 1024 + 1))
        contents = stream.getvalue()
        upload = UploadFile(filename="archive.docx", file=BytesIO(contents), headers={"content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"})
        with pytest.raises(GenerationError) as error:
            extract_upload(upload, contents)
        assert error.value.code == "document_structure_too_large"


def test_generated_output_rejects_duplicate_questions_missing_extra_wrong_count_and_bad_index():
    valid = {
        "questions": [{"prompt": "A?", "options": ["A", "B", "C", "D"], "correct_option_index": 0,
                       "explanation": "Why", "difficulty": "easy", "category": "science"}]
    }
    with pytest.raises(Exception):
        GeneratedQuestions.model_validate_json("not json")
    with pytest.raises(Exception):
        GeneratedQuestions.model_validate({"questions": [{**valid["questions"][0], "surprise": 1}]})
    for invalid in [
        {"questions": [{**valid["questions"][0], "correct_option_index": True}]},
        {"questions": [{**valid["questions"][0], "options": ["A", "a", "C", "D"]}]},
        {"questions": [{**valid["questions"][0], "difficulty": "expert"}]},
        {"questions": [{key: value for key, value in valid["questions"][0].items() if key != "explanation"}]},
    ]:
        with pytest.raises(Exception):
            GeneratedQuestions.model_validate(invalid)


def test_generated_question_normalizes_official_category_id_before_validation():
    question = GeneratedQuestion.model_validate({
        "prompt": "Which protocol resolves a host name to an IP address?",
        "options": ["DNS", "SMTP", "SSH", "NTP"],
        "correct_option_index": 0,
        "explanation": "DNS resolves domain names to network addresses.",
        "difficulty": "medium",
        "category": "category:technology-computing-fundamentals",
    })
    assert question.category == "technology-computing-fundamentals"


@pytest.mark.anyio
async def test_wrong_count_retries_once_then_fails():
    provider = MockProvider(response=json.dumps({"questions": []}))
    service = GenerationService(provider)
    with pytest.raises(GenerationError) as error:
        await service.generate(GenerationRequest(subject="Science", topic="Space", question_count=1, difficulty="easy"), None, "owner-token")
    assert error.value.code == "invalid_generated_output"
    assert provider.calls == 2


@pytest.mark.anyio
async def test_duplicate_questions_rejected_after_independent_validation():
    question = {"prompt": "Same question?", "options": ["A", "B", "C", "D"], "correct_option_index": 0,
                "explanation": "Reason", "difficulty": "easy", "category": "science"}
    provider = MockProvider(response=json.dumps({"questions": [question, question]}))
    service = GenerationService(provider)
    with pytest.raises(GenerationError) as error:
        await service.generate(GenerationRequest(subject="Science", topic="Space", question_count=2, difficulty="easy"), None, "owner")
    assert error.value.code == "invalid_generated_output"
    assert provider.calls == 2


@pytest.mark.anyio
@pytest.mark.parametrize("failure,code", [
    (GenerationError(504, "provider_timeout", "timeout"), "provider_timeout"),
    (GenerationError(429, "provider_rate_limited", "busy"), "provider_rate_limited"),
    (GenerationError(422, "provider_refusal", "refused"), "provider_refusal"),
])
async def test_provider_errors_do_not_retry(failure, code):
    provider = MockProvider(error=failure)
    service = GenerationService(provider)
    with pytest.raises(GenerationError) as error:
        await service.generate(GenerationRequest(subject="Science", topic="Space", question_count=1, difficulty="easy"), None, "owner")
    assert error.value.code == code
    assert provider.calls == 1


@pytest.mark.anyio
async def test_unexpected_timeout_maps_to_safe_error():
    provider = MockProvider(error=TimeoutError())
    service = GenerationService(provider)
    with pytest.raises(GenerationError) as error:
        await service.generate(GenerationRequest(subject="Science", topic="Space", question_count=1, difficulty="easy"), None, "owner")
    assert error.value.code == "provider_timeout"
    assert provider.calls == 1


def test_generation_limiter_allows_five_then_denies():
    limiter = GenerationLimiter()
    assert [limiter.allow("owner") for _ in range(6)] == [True] * 5 + [False]


def test_provider_requires_explicit_free_tier_opt_in_and_allowlisted_model(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret")
    monkeypatch.setenv("AI_GENERATION_ENABLED", "true")
    monkeypatch.setenv("AI_FREE_TIER_ONLY", "false")
    assert GenerationService().provider is None
    monkeypatch.setenv("AI_FREE_TIER_ONLY", "true")
    assert isinstance(GenerationService().provider, GeminiProvider)
    monkeypatch.setenv("GEMINI_MODEL", "gemini-paid-model")
    assert GenerationService().provider is None


def test_provider_stays_disabled_when_backend_api_key_is_missing(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("AI_GENERATION_ENABLED", "true")
    monkeypatch.setenv("AI_FREE_TIER_ONLY", "true")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    assert GenerationService().provider is None
def test_gemini_temperature_is_configurable_and_invalid_values_fall_back(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret")
    monkeypatch.setenv("AI_GENERATION_ENABLED", "true")
    monkeypatch.setenv("AI_FREE_TIER_ONLY", "true")
    monkeypatch.setenv("GEMINI_TEMPERATURE", "1.1")
    assert GenerationService().provider.temperature == 1.1
    monkeypatch.setenv("GEMINI_TEMPERATURE", "99")
    assert GenerationService().provider.temperature == 0.7


@pytest.mark.anyio
async def test_generation_prompt_contains_scope_difficulty_and_diversity_instructions():
    provider = MockProvider()
    service = GenerationService(provider)
    request = GenerationRequest(subject="Physics", topic="Motion", category="Physics", question_count=1, difficulty="hard")
    await service.generate(request, None, "owner")
    prompt = provider.prompts[0]
    assert "science-physics" in prompt and "Motion" in prompt and "hard" in prompt
    assert "distinct sub-concepts" in prompt and "multi-step reasoning" in prompt


def test_generation_api_enforces_five_requests_per_owner_per_hour(ai_client):
    client = ai_client
    service = client.app.state.generation_service
    service.provider = MockProvider()
    owner = client.post("/api/quiz-owners").json()["capability"]
    headers = {"Authorization": f"Bearer {owner}"}
    payload = {"subject": "Science", "topic": "Space", "question_count": 1, "difficulty": "easy"}
    responses = [client.post("/api/ai/generations", headers=headers, json=payload) for _ in range(6)]
    assert [response.status_code for response in responses] == [200] * 5 + [429]
    assert responses[-1].json()["error"]["code"] == "generation_rate_limited"
    assert service.provider.calls == 5

