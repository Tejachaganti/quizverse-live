"""Unit tests for AnswerValidator."""

from __future__ import annotations

import pytest

from quizverse.intelligence.answer_validation import AnswerValidator


def test_valid_basic_answer():
    validator = AnswerValidator()
    result = validator.validate(
        prompt="What is the capital of Japan?",
        options=["Tokyo", "Kyoto", "Osaka", "Nagoya"],
        correct_option_index=0,
        explanation="Tokyo has been the capital of Japan since 1868.",
    )
    assert result.is_valid is True
    assert not any(f.get("status") == "fail" for f in result.findings)


def test_invalid_correct_index_out_of_bounds():
    validator = AnswerValidator()
    result = validator.validate(
        prompt="What is the capital of France?",
        options=["Paris", "Lyon", "Marseille", "Nice"],
        correct_option_index=5,
        explanation="Paris is the capital.",
    )
    assert result.is_valid is False
    assert any("out of bounds" in str(f.get("details", "")).lower() for f in result.findings)


def test_invalid_empty_options():
    validator = AnswerValidator()
    result = validator.validate(
        prompt="Which element is Au?",
        options=["Gold", ""],
        correct_option_index=1,
        explanation="Au is gold.",
    )
    assert result.is_valid is False
    assert any("empty" in str(f.get("details", "")).lower() for f in result.findings)


def test_arithmetic_answer_validation_correct():
    validator = AnswerValidator()
    result = validator.validate(
        prompt="What is 15 * 12?",
        options=["160", "180", "190", "200"],
        correct_option_index=1,
        explanation="15 * 12 = 180.",
    )
    assert result.is_valid is True
    assert result.arithmetic_verified is True


def test_arithmetic_answer_validation_incorrect():
    validator = AnswerValidator()
    result = validator.validate(
        prompt="What is 25 + 75?",
        options=["90", "95", "100", "105"],
        correct_option_index=0,  # Points to '90', but 25+75=100
        explanation="25 + 75 equals 100.",
    )
    assert result.is_valid is False
    assert result.has_contradiction is True
    assert any("contradiction" in str(f.get("details", "")).lower() or "arithmetic" in str(f.get("details", "")).lower() for f in result.findings)


def test_safe_arithmetic_no_eval_injection():
    validator = AnswerValidator()
    # AST parser should safely reject non-arithmetic syntax without executing code
    result = validator.validate(
        prompt="What is __import__('os').system('dir')?",
        options=["0", "1", "2", "3"],
        correct_option_index=0,
        explanation="Not an arithmetic expression.",
    )
    assert result.arithmetic_verified is False


def test_explanation_contradiction_detection():
    validator = AnswerValidator()
    result = validator.validate(
        prompt="Which planet is fourth from the Sun?",
        options=["Venus", "Earth", "Mars", "Jupiter"],
        correct_option_index=0,  # Points to Venus, but explanation says Mars
        explanation="The correct answer is Mars because it is the fourth planet from the Sun.",
    )
    assert result.has_contradiction is True
