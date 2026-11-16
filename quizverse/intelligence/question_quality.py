"""Question intelligence and multi-factor quality scoring engine."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class QualityIssue:
    issue_type: str
    severity: str  # "error", "warning", "info"
    message: str

    @property
    def description(self) -> str:
        return self.message


@dataclass(slots=True)
class QuestionQualityResult:
    quality_score: float
    clarity_score: float
    ambiguity_score: float
    answer_consistency_score: float
    explanation_score: float
    duplicate_risk: str  # "none", "low", "medium", "high"
    difficulty_confidence: float
    topic_relevance: float
    validation_status: str  # "validated", "needs_review", "rejected", "pending"
    review_required: bool
    detected_issues: list[dict[str, str]] = field(default_factory=list)
    suggested_improvements: list[str] = field(default_factory=list)

    @property
    def consistency_score(self) -> float:
        return self.answer_consistency_score

    @property
    def issues(self) -> list[QualityIssue]:
        return [
            QualityIssue(
                issue_type=iss.get("issue_type", "quality_issue"),
                severity=iss.get("severity", "warning"),
                message=iss.get("message") or iss.get("description", ""),
            )
            for iss in self.detected_issues
        ]

    @property
    def suggestions(self) -> list[str]:
        return self.suggested_improvements

    @property
    def verdict(self) -> str:
        if self.quality_score >= 80:
            return "excellent"
        if self.quality_score >= 65:
            return "good"
        if self.quality_score >= 50:
            return "fair"
        return "needs_revision"

    def to_dict(self) -> dict[str, Any]:
        return {
            "quality_score": round(self.quality_score, 1),
            "clarity_score": round(self.clarity_score, 1),
            "ambiguity_score": round(self.ambiguity_score, 1),
            "answer_consistency_score": round(self.answer_consistency_score, 1),
            "consistency_score": round(self.answer_consistency_score, 1),
            "explanation_score": round(self.explanation_score, 1),
            "duplicate_risk": self.duplicate_risk,
            "difficulty_confidence": round(self.difficulty_confidence, 2),
            "topic_relevance": round(self.topic_relevance, 2),
            "validation_status": self.validation_status,
            "review_required": self.review_required,
            "detected_issues": self.detected_issues,
            "suggested_improvements": self.suggested_improvements,
            "issues": [iss.get("message") or iss.get("description", "") for iss in self.detected_issues],
            "suggestions": self.suggested_improvements,
            "verdict": self.verdict,
        }


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text or "").casefold().split())


class QuestionQualityAnalyzer:
    """Evaluates multiple educational and grammatical quality dimensions deterministically."""

    # Interrogative starter words
    INTERROGATIVES = {
        "what", "which", "who", "whom", "whose", "where", "when", "why", "how",
        "name", "identify", "calculate", "determine", "find", "select", "choose",
        "in", "if", "during", "according", "explain", "compute"
    }

    # Negative phrase patterns that increase ambiguity
    DOUBLE_NEGATIVE_PATTERNS = [
        re.compile(r"\bnot\b.*\b(un|in|dis|im|non)[a-z]+", re.IGNORECASE),
        re.compile(r"\bnot\b.*\b(incorrect|untrue|false|never|without)\b", re.IGNORECASE),
        re.compile(r"\bexcept\b.*\bnot\b", re.IGNORECASE),
        re.compile(r"\bneither\b.*\bnor\b.*\bnot\b", re.IGNORECASE),
    ]

    # Placeholder patterns
    PLACEHOLDER_PATTERNS = [
        re.compile(r"\[.*?\]"),
        re.compile(r"<.*?>"),
        re.compile(r"\b(todo|tbd|placeholder|insert here|lorem ipsum)\b", re.IGNORECASE),
        re.compile(r"\.{4,}"),  # 4 or more dots
    ]

    # Category vocabulary hints for topic relevance check
    CATEGORY_LEXICONS = {
        "science": {"planet", "cell", "gas", "atom", "molecule", "energy", "force", "chemical", "physics", "biology", "organism", "element", "water", "temperature", "earth", "sun", "speed", "light", "gravity", "species"},
        "technology": {"cpu", "computer", "software", "hardware", "network", "internet", "code", "programming", "data", "database", "algorithm", "html", "css", "python", "web", "server", "memory", "processor", "device", "file"},
        "geography": {"capital", "country", "city", "ocean", "river", "mountain", "continent", "border", "island", "desert", "region", "lake", "sea", "climate", "hemisphere", "map", "population", "state"},
        "history": {"war", "century", "empire", "king", "queen", "president", "ancient", "revolution", "treaty", "civilization", "dynasty", "battle", "declaration", "constitution", "era", "founded"},
        "literature": {"novel", "author", "wrote", "book", "character", "play", "poem", "poet", "writer", "literature", "tragedy", "shakespeare", "story", "literary"},
        "mathematics": {"number", "sum", "product", "equation", "angle", "triangle", "fraction", "ratio", "percent", "prime", "root", "integer", "calculate", "value", "graph", "formula", "algebra", "geometry"},
        "nature": {"animal", "mammal", "species", "forest", "wildlife", "predator", "ocean", "tree", "plant", "fastest", "largest", "bird", "reptile", "habitat"},
    }

    def analyze(
        self,
        prompt: str,
        options: list[str] | tuple[str, ...],
        correct_option_index: int,
        explanation: str = "",
        category: str = "",
        difficulty: str = "medium",
    ) -> QuestionQualityResult:
        issues: list[QualityIssue] = []
        improvements: list[str] = []

        clean_prompt = (prompt or "").strip()
        clean_explanation = (explanation or "").strip()
        norm_options = [str(opt or "").strip() for opt in options]

        # -------------------------------------------------------------
        # 1. Answer Consistency & Integrity (Base 100)
        # -------------------------------------------------------------
        consistency_score = 100.0
        if not norm_options or len(norm_options) < 2:
            consistency_score -= 80.0
            issues.append(QualityIssue("missing_options", "error", "Question must have at least 2 distinct answer options."))
        elif len(norm_options) < 4:
            consistency_score -= 15.0
            issues.append(QualityIssue("few_options", "info", "Standard multiple-choice format recommends 4 options."))

        if correct_option_index < 0 or correct_option_index >= len(norm_options):
            consistency_score = 0.0
            issues.append(QualityIssue("invalid_answer_index", "error", f"Correct option index {correct_option_index} is out of bounds for {len(norm_options)} options."))
        else:
            correct_val = norm_options[correct_option_index]
            if not correct_val:
                consistency_score = 0.0
                issues.append(QualityIssue("empty_correct_option", "error", "The designated correct answer option is empty."))

        # Check for empty options
        empty_opts = [i for i, opt in enumerate(norm_options) if not opt]
        if empty_opts:
            consistency_score -= 30.0
            issues.append(QualityIssue("empty_options", "error", f"Options at index {empty_opts} are empty."))

        # Check duplicate options
        casefolded_options = [_normalize(opt) for opt in norm_options if opt]
        if len(set(casefolded_options)) < len(casefolded_options):
            consistency_score -= 40.0
            issues.append(QualityIssue("duplicate_options", "error", "Answer options contain duplicate or near-identical text."))
            improvements.append("Ensure all answer options are distinct and provide meaningful distractors.")

        consistency_score = max(0.0, min(100.0, consistency_score))

        # -------------------------------------------------------------
        # 2. Clarity & Readability (Base 100)
        # -------------------------------------------------------------
        clarity_score = 100.0
        prompt_len = len(clean_prompt)
        words = clean_prompt.split()

        if prompt_len < 10:
            clarity_score -= 50.0
            issues.append(QualityIssue("prompt_too_short", "error", "Prompt is excessively short (< 10 characters)."))
            improvements.append("Expand the prompt to provide clear context for the question.")
        elif prompt_len < 20:
            clarity_score -= 20.0
            issues.append(QualityIssue("prompt_brief", "warning", "Prompt is very brief; consider providing more context."))
        elif prompt_len > 400:
            clarity_score -= 20.0
            issues.append(QualityIssue("prompt_long", "info", "Prompt is lengthy; check whether sentence structure can be tightened."))

        # Punctuation check
        if clean_prompt and not clean_prompt.endswith(("?", ".", ":")):
            clarity_score -= 10.0
            issues.append(QualityIssue("missing_punctuation", "warning", "Prompt does not end with standard punctuation (? or .)."))
            improvements.append("Add a question mark or period at the end of the question prompt.")

        # Interrogative check
        first_word = words[0].lower().rstrip("?,.:;\"'") if words else ""
        if first_word not in self.INTERROGATIVES and not clean_prompt.endswith("?"):
            clarity_score -= 15.0
            improvements.append("Begin the question with a clear interrogative word (e.g. 'Which', 'What', 'How') or end with a question mark.")

        # Placeholder detection
        for pat in self.PLACEHOLDER_PATTERNS:
            if pat.search(clean_prompt):
                clarity_score -= 35.0
                issues.append(QualityIssue("placeholder_found", "error", "Prompt contains placeholder or unfinished template text."))
                improvements.append("Remove template placeholders and write complete question text.")
                break

        # Distractor length bias check (one option much longer than others)
        opt_lengths = [len(opt) for opt in norm_options if opt]
        if opt_lengths and max(opt_lengths) > 4 * min(opt_lengths) and max(opt_lengths) > 40:
            clarity_score -= 15.0
            issues.append(QualityIssue("option_length_bias", "warning", "One answer option is disproportionately longer than others, which can cue test-takers."))
            improvements.append("Balance the length and grammatical structure of all answer options.")

        # Cliché distractors
        cliche_opts = [opt for opt in norm_options if _normalize(opt) in {"all of the above", "none of the above", "both a and b"}]
        if cliche_opts:
            clarity_score -= 10.0
            issues.append(QualityIssue("cliche_distractors", "info", "Avoid 'All of the above' or 'None of the above' options in high-quality items."))

        clarity_score = max(0.0, min(100.0, clarity_score))

        # -------------------------------------------------------------
        # 3. Ambiguity & Negative Phrasing (Base 100, 100 = least ambiguous)
        # -------------------------------------------------------------
        ambiguity_score = 100.0

        for pattern in self.DOUBLE_NEGATIVE_PATTERNS:
            if pattern.search(clean_prompt):
                ambiguity_score -= 35.0
                issues.append(QualityIssue("double_negative", "warning", "Question contains double-negative or convoluted negative phrasing."))
                improvements.append("Rephrase the question into direct positive wording instead of negative conditionals.")
                break

        # Vague pronoun phrasing
        if re.search(r"\bwhich (one|item|thing) of these is it\b", clean_prompt, re.IGNORECASE):
            ambiguity_score -= 20.0
            issues.append(QualityIssue("vague_reference", "warning", "Question uses vague references ('which one of these')."))

        ambiguity_score = max(0.0, min(100.0, ambiguity_score))

        # -------------------------------------------------------------
        # 4. Explanation Completeness (Base 100)
        # -------------------------------------------------------------
        explanation_score = 100.0
        if not clean_explanation:
            explanation_score = 0.0
            issues.append(QualityIssue("missing_explanation", "warning", "Question lacks an explanation for why the answer is correct."))
            improvements.append("Provide a concise 1-2 sentence explanation clarifying the correct answer.")
        elif len(clean_explanation) < 20:
            explanation_score -= 50.0
            issues.append(QualityIssue("brief_explanation", "info", "Explanation is very short (< 20 characters)."))
            improvements.append("Elaborate on the rationale behind the correct solution.")
        else:
            # Check if explanation references correct answer
            if 0 <= correct_option_index < len(norm_options):
                correct_text = _normalize(norm_options[correct_option_index])
                norm_expl = _normalize(clean_explanation)
                # If correct answer is single word/number or phrase, does explanation reference it?
                first_key = correct_text.split()[0] if correct_text.split() else ""
                if first_key and first_key in norm_expl:
                    explanation_score = min(100.0, explanation_score + 5.0)

        explanation_score = max(0.0, min(100.0, explanation_score))

        # -------------------------------------------------------------
        # 5. Topic Relevance & Difficulty Confidence
        # -------------------------------------------------------------
        topic_relevance = 0.85
        norm_cat = _normalize(category)
        matched_lex = None
        for cat_key, lexicon in self.CATEGORY_LEXICONS.items():
            if cat_key in norm_cat:
                matched_lex = lexicon
                break

        if matched_lex:
            prompt_norm = _normalize(clean_prompt)
            hits = sum(1 for term in matched_lex if term in prompt_norm)
            if hits >= 2:
                topic_relevance = 1.0
            elif hits == 1:
                topic_relevance = 0.9
            else:
                topic_relevance = 0.75
                improvements.append(f"Consider including key concepts directly relevant to {category}.")

        difficulty_confidence = 0.90
        diff_val = (difficulty or "medium").lower()
        if diff_val == "easy" and prompt_len > 250:
            difficulty_confidence = 0.75
            improvements.append("For 'easy' difficulty, simplify wording and reduce prompt length.")
        elif diff_val == "hard" and prompt_len < 30 and len(norm_options) <= 4 and max((len(o) for o in norm_options), default=0) < 10:
            difficulty_confidence = 0.75
            improvements.append("For 'hard' difficulty, consider multi-step problem solving or more challenging distractors.")

        # -------------------------------------------------------------
        # 6. Duplicate Risk
        # -------------------------------------------------------------
        duplicate_risk = "none"
        if len(set(casefolded_options)) < len(casefolded_options):
            duplicate_risk = "high"

        # -------------------------------------------------------------
        # 7. Overall Weighted Quality Score
        # -------------------------------------------------------------
        overall_score = (
            clarity_score * 0.30
            + consistency_score * 0.35
            + ambiguity_score * 0.15
            + explanation_score * 0.20
        )
        overall_score = round(max(0.0, min(100.0, overall_score)), 1)

        has_errors = any(i.severity == "error" for i in issues)
        has_warnings = any(i.severity == "warning" for i in issues)

        if has_errors or overall_score < 50.0:
            validation_status = "rejected"
            review_required = True
        elif has_warnings or overall_score < 75.0:
            validation_status = "needs_review"
            review_required = True
        else:
            validation_status = "validated"
            review_required = False

        return QuestionQualityResult(
            quality_score=overall_score,
            clarity_score=clarity_score,
            ambiguity_score=ambiguity_score,
            answer_consistency_score=consistency_score,
            explanation_score=explanation_score,
            duplicate_risk=duplicate_risk,
            difficulty_confidence=difficulty_confidence,
            topic_relevance=topic_relevance,
            validation_status=validation_status,
            review_required=review_required,
            detected_issues=[{"type": i.issue_type, "severity": i.severity, "message": i.message} for i in issues],
            suggested_improvements=improvements,
        )
