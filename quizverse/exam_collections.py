"""Small curated exam collections mapped onto the shared question taxonomy."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExamCollectionDefinition:
    slug: str
    display_name: str
    description: str
    category_slugs: tuple[str, ...]

    @property
    def id(self) -> str:
        return f"exam:{self.slug}"

    @property
    def name_key(self) -> str:
        return f"taxonomy.exam_collection.{self.slug}"


EXAM_COLLECTIONS: tuple[ExamCollectionDefinition, ...] = (
    ExamCollectionDefinition(
        "school-learning", "School Learning", "Curated school-learning themes across core subjects.",
        ("mathematics", "verbal", "science", "general_knowledge", "culture"),
    ),
    ExamCollectionDefinition(
        "college-learning", "College Learning", "Curated subject themes for college-level learning.",
        ("mathematics", "science", "technology", "verbal", "culture"),
    ),
    ExamCollectionDefinition(
        "campus-placements", "Campus Placements", "Practice themes for common campus placement preparation.",
        ("mathematics-quantitative-aptitude", "reasoning-analytical-problems", "verbal-vocabulary", "technology-programming"),
    ),
    ExamCollectionDefinition(
        "software-interviews", "Software Interviews", "Practice themes for software interview preparation.",
        ("technology-computing-fundamentals", "technology-programming", "reasoning-analytical-problems", "mathematics-quantitative-aptitude"),
    ),
    ExamCollectionDefinition(
        "rrb-ntpc", "RRB NTPC", "Curated general preparation themes; not an official syllabus.",
        ("mathematics-quantitative-aptitude", "reasoning-analytical-problems", "verbal-reading-comprehension", "general_knowledge-geography", "general_knowledge-history-civics", "general_knowledge-current-affairs"),
    ),
    ExamCollectionDefinition(
        "ssc", "SSC", "Curated general preparation themes; not an official syllabus.",
        ("mathematics-quantitative-aptitude", "reasoning-analytical-problems", "verbal-vocabulary", "general_knowledge-geography", "general_knowledge-history-civics", "general_knowledge-current-affairs"),
    ),
    ExamCollectionDefinition(
        "banking", "Banking", "Curated quantitative, reasoning, language, and awareness themes.",
        ("mathematics-quantitative-aptitude", "reasoning-analytical-problems", "verbal-reading-comprehension", "general_knowledge-current-affairs", "everyday_knowledge-money-consumers"),
    ),
    ExamCollectionDefinition(
        "upsc-fundamentals", "UPSC Fundamentals", "Curated introductory study themes; not an official syllabus.",
        ("general_knowledge-history-civics", "general_knowledge-geography", "general_knowledge-current-affairs", "science", "culture", "reasoning-analytical-problems"),
    ),
    ExamCollectionDefinition(
        "general-competitive-exams", "General Competitive Exams", "Shared practice themes commonly used in competitive-exam preparation.",
        ("mathematics-quantitative-aptitude", "reasoning", "verbal", "general_knowledge", "science"),
    ),
)
