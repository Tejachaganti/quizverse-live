"""Unit tests for QuestionQualityAnalyzer."""

from __future__ import annotations

import pytest

from quizverse.intelligence.question_quality import QuestionQualityAnalyzer


def test_high_quality_question():
    analyzer = QuestionQualityAnalyzer()
    result = analyzer.analyze(
        prompt="What is the primary function of ribosomes in eukaryotic cells?",
        options=[
            "Cellular protein synthesis",
            "Lipid bilayer synthesis",
            "DNA replication and repair",
            "Cell wall carbohydrate synthesis",
        ],
        correct_option_index=0,
        explanation="Ribosomes are macromolecular machines found within all cells that perform biological protein synthesis according to mRNA transcripts.",
        category="Biology",
        difficulty="medium",
    )
    assert result.quality_score >= 80.0
    assert result.clarity_score >= 80.0
    assert result.explanation_score >= 80.0
    assert len(result.issues) == 0
    assert result.verdict in ("excellent", "good")


def test_short_prompt_penalized():
    analyzer = QuestionQualityAnalyzer()
    result = analyzer.analyze(
        prompt="DNA?",
        options=["Acid", "Base", "Sugar", "Fat"],
        correct_option_index=0,
        explanation="DNA stands for deoxyribonucleic acid.",
        category="Science",
        difficulty="easy",
    )
    assert any("short" in issue.description.lower() for issue in result.issues)
    assert result.clarity_score < 80.0


def test_placeholder_options_detected():
    analyzer = QuestionQualityAnalyzer()
    result = analyzer.analyze(
        prompt="Which element has the atomic number 1 in the periodic table?",
        options=[
            "Hydrogen",
            "None of the above",
            "All of the above",
            "I don't know",
        ],
        correct_option_index=0,
        explanation="Hydrogen is the first chemical element in the periodic table with atomic number 1.",
        category="Chemistry",
        difficulty="easy",
    )
    assert any("none of the above" in issue.description.lower() or "all of the above" in issue.description.lower() or "placeholder" in issue.description.lower() for issue in result.issues)
    assert result.clarity_score < 100.0


def test_length_bias_warning():
    analyzer = QuestionQualityAnalyzer()
    result = analyzer.analyze(
        prompt="What is photosystem II in plant photosynthesis?",
        options=[
            "Photosystem II is the first protein complex in the light-dependent reactions of oxygenic photosynthesis that captures photons and energizes electrons extracted from water molecules",
            "A sugar",
            "A cell",
            "A root",
        ],
        correct_option_index=0,
        explanation="Photosystem II splits water and releases oxygen.",
        category="Botany",
        difficulty="hard",
    )
    assert any("longer" in issue.description.lower() or "length" in issue.description.lower() for issue in result.issues)


def test_duplicate_options_detected():
    analyzer = QuestionQualityAnalyzer()
    result = analyzer.analyze(
        prompt="Which gas is most abundant in Earth's atmosphere?",
        options=["Nitrogen", "Oxygen", "Nitrogen", "Argon"],
        correct_option_index=0,
        explanation="Nitrogen makes up roughly 78 percent of Earth's atmosphere.",
        category="Earth Science",
        difficulty="medium",
    )
    assert any("duplicate" in issue.description.lower() for issue in result.issues)
    assert result.consistency_score < 70.0


def test_missing_explanation_penalized():
    analyzer = QuestionQualityAnalyzer()
    result = analyzer.analyze(
        prompt="Which planet in the solar system is known as the Red Planet?",
        options=["Venus", "Mars", "Jupiter", "Mercury"],
        correct_option_index=1,
        explanation="",
        category="Astronomy",
        difficulty="easy",
    )
    assert any("explanation" in issue.description.lower() for issue in result.issues)
    assert result.explanation_score == 0.0
