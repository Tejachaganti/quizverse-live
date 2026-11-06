"""Isolated deterministic provider app used only by browser verification."""

from __future__ import annotations

import json
import re

from quizverse.persistence.db import Database
from quizverse.realtime import create_app


class BrowserMockProvider:
    async def generate(self, prompt: str, schema: dict, *, max_output_tokens: int) -> str:
        questions = []
        for index in range(int(re.search(r"Requested count: (\d+)", prompt).group(1))):
            questions.append({
                "prompt": f"Mock generated question {index + 1}?",
                "options": [f"Option {index + 1} A", f"Option {index + 1} B", f"Option {index + 1} C", f"Option {index + 1} D"],
                "correct_option_index": 1,
                "explanation": "Option B is correct in this deterministic browser fixture.",
                "difficulty": "medium",
                "category": "browser-check",
            })
        return json.dumps({"questions": questions})


app = create_app(database=Database(), ai_provider=BrowserMockProvider())
