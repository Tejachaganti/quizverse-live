"""Multi-level duplicate question detection: exact, normalized, near-duplicate, and semantic."""

from __future__ import annotations

import difflib
import hashlib
import re
import string
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(slots=True)
class DuplicateCandidate:
    id: str
    prompt: str
    category: str = ""
    difficulty: str = "medium"


@dataclass(slots=True)
class DuplicateCheckResult:
    duplicate_status: str  # "unique", "exact_duplicate", "normalized_duplicate", "near_duplicate", "semantic_similar"
    similarity_score: float  # 0.0 to 1.0
    matched_question_id: str | None
    duplicate_reason: str
    review_required: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "duplicate_status": self.duplicate_status,
            "similarity_score": round(self.similarity_score, 2),
            "matched_question_id": self.matched_question_id,
            "duplicate_reason": self.duplicate_reason,
            "review_required": self.review_required,
        }


def _strip_punctuation(text: str) -> str:
    return text.translate(str.maketrans("", "", string.punctuation))


def _normalize_text(text: str) -> str:
    nfc = unicodedata.normalize("NFC", text or "")
    clean = _strip_punctuation(nfc).casefold()
    return " ".join(clean.split())


def _token_set(text: str) -> set[str]:
    stopwords = {"what", "which", "is", "the", "of", "a", "an", "in", "to", "for", "and", "or", "by", "from", "on", "are", "was", "were"}
    tokens = set(_normalize_text(text).split())
    return tokens - stopwords or tokens


def _jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


class DuplicateDetector:
    """Evaluates question similarity across lexical, syntactic, and structural tiers."""

    EXACT_THRESHOLD = 1.0
    NORMALIZED_THRESHOLD = 0.98
    NEAR_DUPLICATE_RATIO = 0.85
    SEMANTIC_TOKEN_RATIO = 0.70

    def check_duplicate(
        self,
        prompt: str,
        candidates: Iterable[DuplicateCandidate | dict[str, Any]],
        exclude_id: str | None = None,
    ) -> DuplicateCheckResult:
        raw_prompt = (prompt or "").strip()
        if not raw_prompt:
            return DuplicateCheckResult("unique", 0.0, None, "Empty prompt provided.", False)

        prompt_hash = hashlib.sha256(raw_prompt.encode("utf-8")).hexdigest()
        normalized_target = _normalize_text(raw_prompt)
        tokens_target = _token_set(raw_prompt)

        best_score = 0.0
        best_match_id = None
        best_status = "unique"
        best_reason = "No matching duplicates found."

        for cand in candidates:
            c_id = cand["id"] if isinstance(cand, dict) else cand.id
            if exclude_id and c_id == exclude_id:
                continue

            c_prompt = cand["prompt"] if isinstance(cand, dict) else cand.prompt
            c_clean = (c_prompt or "").strip()
            if not c_clean:
                continue

            # 1. Exact text duplicate
            if raw_prompt == c_clean or prompt_hash == hashlib.sha256(c_clean.encode("utf-8")).hexdigest():
                return DuplicateCheckResult(
                    duplicate_status="exact_duplicate",
                    similarity_score=1.0,
                    matched_question_id=c_id,
                    duplicate_reason=f"Exact match with existing question {c_id}.",
                    review_required=True,
                )

            # 2. Normalized text duplicate
            normalized_cand = _normalize_text(c_clean)
            if normalized_target == normalized_cand:
                return DuplicateCheckResult(
                    duplicate_status="normalized_duplicate",
                    similarity_score=0.99,
                    matched_question_id=c_id,
                    duplicate_reason=f"Matches existing question {c_id} ignoring case, punctuation, and whitespace.",
                    review_required=True,
                )

            # 3. Near-duplicate wording (SequenceMatcher / Levenshtein proxy)
            seq_ratio = difflib.SequenceMatcher(None, normalized_target, normalized_cand).ratio()
            tokens_cand = _token_set(c_clean)
            jaccard = _jaccard_similarity(tokens_target, tokens_cand)

            combined_similarity = max(seq_ratio, (seq_ratio * 0.6 + jaccard * 0.4))

            if combined_similarity > best_score:
                best_score = combined_similarity
                best_match_id = c_id

                if seq_ratio >= self.NEAR_DUPLICATE_RATIO:
                    best_status = "near_duplicate"
                    best_reason = f"Near-duplicate wording ({round(seq_ratio * 100)}% lexical similarity) with question {c_id}."
                elif jaccard >= self.SEMANTIC_TOKEN_RATIO:
                    best_status = "semantic_similar"
                    best_reason = f"High conceptual and keyword overlap ({round(jaccard * 100)}% keyword overlap) with question {c_id}."

        review_required = best_status in {"exact_duplicate", "normalized_duplicate", "near_duplicate"}

        return DuplicateCheckResult(
            duplicate_status=best_status,
            similarity_score=best_score,
            matched_question_id=best_match_id if best_status != "unique" else None,
            duplicate_reason=best_reason if best_status != "unique" else "Question appears unique compared to existing bank.",
            review_required=review_required,
        )
