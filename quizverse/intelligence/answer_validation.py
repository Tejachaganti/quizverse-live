"""Answer and explanation consistency validator with deterministic mathematical verification."""

from __future__ import annotations

import ast
import operator
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ValidationFinding:
    rule_name: str
    status: str  # "pass", "fail", "warning"
    details: str

    @property
    def message(self) -> str:
        return self.details

    def to_dict(self) -> dict[str, str]:
        return {
            "rule_name": self.rule_name,
            "status": self.status,
            "details": self.details,
            "message": self.details,
        }


@dataclass(slots=True)
class AnswerValidationResult:
    is_valid: bool
    review_status: str  # "validated", "needs_review", "rejected", "pending"
    confidence: float
    has_contradiction: bool
    findings: list[dict[str, str]] = field(default_factory=list)
    suggested_correction: str | None = None
    arithmetic_verified: bool = False
    explanation_aligned: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "review_status": self.review_status,
            "confidence": round(self.confidence, 2),
            "has_contradiction": self.has_contradiction,
            "findings": self.findings,
            "suggested_correction": self.suggested_correction,
            "arithmetic_verified": self.arithmetic_verified,
            "explanation_aligned": self.explanation_aligned,
        }


def _clean_str(val: str) -> str:
    return " ".join(unicodedata.normalize("NFC", val or "").casefold().split())


class SafeArithmeticEvaluator:
    """Safely evaluates basic arithmetic expressions without eval() or dangerous execution."""

    SAFE_OPERATORS = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
    }

    ARITHMETIC_PROMPT_REGEX = re.compile(
        r"(?:what\s+is|calculate|evaluate|compute|find\s+the\s+value\s+of)\s+([0-9\s\+\-\*\/\^\(\)\.]+)\??",
        re.IGNORECASE,
    )

    def extract_and_evaluate(self, prompt: str) -> float | None:
        match = self.ARITHMETIC_PROMPT_REGEX.search(prompt)
        if not match:
            # Also check direct expression like "12 * 15 = ?" or "25 + 35"
            direct_match = re.search(r"^\s*([0-9\s\+\-\*\/\^\(\)\.]+)\s*(?:=\s*\?|\?|$)", prompt)
            if direct_match and any(op in direct_match.group(1) for op in "+-*/^"):
                match = direct_match

        if not match:
            return None

        expr_str = match.group(1).replace("^", "**").strip()
        try:
            node = ast.parse(expr_str, mode="eval")
            return self._eval_node(node.body)
        except Exception:
            return None

    def _eval_node(self, node: ast.AST) -> float:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -self._eval_node(node.operand)
        elif isinstance(node, ast.BinOp):
            op_type = type(node.op)
            if op_type in self.SAFE_OPERATORS:
                left = self._eval_node(node.left)
                right = self._eval_node(node.right)
                if op_type in (ast.Div, ast.FloorDiv, ast.Mod) and right == 0:
                    raise ZeroDivisionError
                return float(self.SAFE_OPERATORS[op_type](left, right))
        raise ValueError("Unsupported AST construct")


class AnswerValidator:
    """Validates answer correctness, option references, and explanation alignment."""

    def __init__(self) -> None:
        self.arithmetic = SafeArithmeticEvaluator()

    def validate(
        self,
        prompt: str,
        options: list[str] | tuple[str, ...],
        correct_option_index: int,
        explanation: str = "",
        format_payload: dict[str, Any] | None = None,
    ) -> AnswerValidationResult:
        findings: list[ValidationFinding] = []
        has_contradiction = False
        suggested_correction = None
        arithmetic_verified = False

        norm_options = [str(opt or "").strip() for opt in options]
        clean_prompt = (prompt or "").strip()
        clean_explanation = (explanation or "").strip()

        # 1. Option Bounds and Selection Validity
        if not norm_options:
            findings.append(ValidationFinding("options_exist", "fail", "No answer options were provided."))
            return AnswerValidationResult(False, "rejected", 0.0, True, [f.to_dict() for f in findings])

        if correct_option_index < 0 or correct_option_index >= len(norm_options):
            findings.append(ValidationFinding("correct_index_in_bounds", "fail", f"Correct option index {correct_option_index} out of bounds."))
            return AnswerValidationResult(False, "rejected", 0.0, True, [f.to_dict() for f in findings])

        correct_text = norm_options[correct_option_index]
        if not correct_text:
            findings.append(ValidationFinding("correct_option_non_empty", "fail", "Correct answer option is empty."))
            return AnswerValidationResult(False, "rejected", 0.0, True, [f.to_dict() for f in findings])
        else:
            findings.append(ValidationFinding("correct_index_in_bounds", "pass", "Correct answer is properly bound to an existing option."))

        # 2. Mathematical / Arithmetic Deterministic Validation
        calculated_val = self.arithmetic.extract_and_evaluate(clean_prompt)
        if calculated_val is not None:
            # Extract number from designated correct answer option
            num_match = re.search(r"[-+]?[0-9]*\.?[0-9]+", correct_text)
            if num_match:
                try:
                    option_val = float(num_match.group(0))
                    # Check if floating point is close
                    if abs(calculated_val - option_val) < 1e-4:
                        arithmetic_verified = True
                        findings.append(ValidationFinding("arithmetic_verification", "pass", f"Mathematical expression evaluated to {calculated_val}, which matches the correct option."))
                    else:
                        has_contradiction = True
                        findings.append(ValidationFinding(
                            "arithmetic_verification",
                            "fail",
                            f"Mathematical contradiction: Expression evaluates to {calculated_val}, but option {correct_option_index} ({correct_text}) was marked correct.",
                        ))
                        # Check if any other option has the calculated value
                        for i, opt in enumerate(norm_options):
                            opt_match = re.search(r"[-+]?[0-9]*\.?[0-9]+", opt)
                            if opt_match and abs(float(opt_match.group(0)) - calculated_val) < 1e-4:
                                suggested_correction = f"Change correct answer to option {i} ('{opt}')."
                                break
                except ValueError:
                    pass

        # 3. Explanation Consistency Check
        if clean_explanation:
            norm_expl = _clean_str(clean_explanation)
            norm_correct = _clean_str(correct_text)

            # Check if explanation explicitly names a different option as correct
            # e.g., explanation says "The correct answer is Option A / Sydney", but index is 1 (Melbourne)
            for i, opt in enumerate(norm_options):
                if i != correct_option_index and opt:
                    norm_other = _clean_str(opt)
                    # Check if explanation says "Option <letter> is correct" or explicitly affirms other option
                    other_patterns = [
                        f"{norm_other} is the correct",
                        f"correct answer is {norm_other}",
                        f"the answer is {norm_other}",
                    ]
                    if any(p in norm_expl for p in other_patterns) and norm_correct not in norm_expl:
                        has_contradiction = True
                        findings.append(ValidationFinding(
                            "explanation_consistency",
                            "warning",
                            f"Explanation appears to state '{opt}' is correct, but option {correct_option_index} ('{correct_text}') is selected.",
                        ))
                        suggested_correction = f"Align selected answer with explanation reference ('{opt}')."
                        break

            if not has_contradiction:
                findings.append(ValidationFinding("explanation_consistency", "pass", "Explanation aligns with selected answer."))
        else:
            findings.append(ValidationFinding("explanation_present", "warning", "No explanation provided to validate."))

        # 4. Open-ended / Puzzle Payload Validation where provided
        if format_payload:
            if "canonical_answer" in format_payload:
                canon = str(format_payload["canonical_answer"]).strip()
                if canon and _clean_str(canon) != _clean_str(correct_text):
                    findings.append(ValidationFinding("canonical_answer_match", "info", "Format payload has canonical answer distinct from selected option."))

        # Final Status determination
        has_failures = any(f.status == "fail" for f in findings)
        has_warnings = any(f.status == "warning" for f in findings)

        if has_failures or has_contradiction:
            review_status = "rejected" if has_failures else "needs_review"
            confidence = 0.4 if has_failures else 0.6
            is_valid = False
        elif has_warnings:
            review_status = "needs_review"
            confidence = 0.75
            is_valid = True
        else:
            review_status = "validated"
            confidence = 0.95
            is_valid = True

        return AnswerValidationResult(
            is_valid=is_valid,
            review_status=review_status,
            confidence=confidence,
            has_contradiction=has_contradiction,
            findings=[f.to_dict() for f in findings],
            suggested_correction=suggested_correction,
            arithmetic_verified=arithmetic_verified,
            explanation_aligned=not has_contradiction,
        )
