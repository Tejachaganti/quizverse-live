"""Stable taxonomy definitions and conservative legacy-category mapping."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CategoryDefinition:
    slug: str
    display_name: str
    parent_slug: str | None = None
    sort_order: int = 0

    @property
    def id(self) -> str:
        return f"category:{self.slug}"

    @property
    def name_key(self) -> str:
        return f"taxonomy.category.{self.slug}"


@dataclass(frozen=True, slots=True)
class TagDefinition:
    slug: str
    display_name: str

    @property
    def id(self) -> str:
        return f"tag:{self.slug}"

    @property
    def name_key(self) -> str:
        return f"taxonomy.tag.{self.slug}"


def _category(slug: str, name: str, parent: str | None = None, order: int = 0) -> CategoryDefinition:
    return CategoryDefinition(slug, name, parent, order)


CATEGORIES: tuple[CategoryDefinition, ...] = (
    _category("technology", "Technology & Computing", order=10),
    _category("technology-computing-fundamentals", "Computing Fundamentals", "technology", 10),
    _category("technology-programming", "Programming", "technology", 20),
    _category("technology-internet-networks", "Internet & Networks", "technology", 30),
    _category("technology-digital-safety", "Digital Safety", "technology", 40),
    _category("mathematics", "Mathematics & Quantitative Aptitude", order=20),
    _category("mathematics-arithmetic", "Arithmetic", "mathematics", 10),
    _category("mathematics-algebra-geometry", "Algebra & Geometry", "mathematics", 20),
    _category("mathematics-quantitative-aptitude", "Quantitative Aptitude", "mathematics", 30),
    _category("reasoning", "Logical & Analytical Reasoning", order=30),
    _category("reasoning-logic-puzzles", "Logic Puzzles", "reasoning", 10),
    _category("reasoning-patterns-sequences", "Patterns & Sequences", "reasoning", 20),
    _category("reasoning-analytical-problems", "Analytical Problems", "reasoning", 30),
    _category("verbal", "English & Verbal Ability", order=40),
    _category("verbal-vocabulary", "Vocabulary", "verbal", 10),
    _category("verbal-grammar-usage", "Grammar & Usage", "verbal", 20),
    _category("verbal-reading-comprehension", "Reading Comprehension", "verbal", 30),
    _category("science", "Science & Discovery", order=50),
    _category("science-biology", "Biology", "science", 10),
    _category("science-chemistry", "Chemistry", "science", 20),
    _category("science-physics", "Physics", "science", 30),
    _category("science-space-astronomy", "Space & Astronomy", "science", 40),
    _category("general_knowledge", "General Knowledge & Awareness", order=60),
    _category("general_knowledge-geography", "Geography & Places", "general_knowledge", 10),
    _category("general_knowledge-history-civics", "History & Civics", "general_knowledge", 20),
    _category("general_knowledge-current-affairs", "Current Affairs", "general_knowledge", 30),
    _category("general_knowledge-world-facts", "World Facts", "general_knowledge", 40),
    _category("sports", "Sports & Fitness", order=70),
    _category("sports-rules-events", "Sports Rules & Events", "sports", 10),
    _category("sports-athletes-teams", "Athletes & Teams", "sports", 20),
    _category("sports-fitness-wellbeing", "Fitness & Wellbeing", "sports", 30),
    _category("entertainment", "Entertainment & Pop Culture", order=80),
    _category("entertainment-film-television", "Film & Television", "entertainment", 10),
    _category("entertainment-music", "Popular Music", "entertainment", 20),
    _category("entertainment-games-pop-culture", "Games & Pop Culture", "entertainment", 30),
    _category("nature", "Nature & Environment", order=90),
    _category("nature-animals", "Animals", "nature", 10),
    _category("nature-plants-ecosystems", "Plants & Ecosystems", "nature", 20),
    _category("nature-environment-climate", "Environment & Climate", "nature", 30),
    _category("culture", "Culture, Arts & Literature", order=100),
    _category("culture-literature", "Literature", "culture", 10),
    _category("culture-visual-arts", "Visual Arts", "culture", 20),
    _category("culture-performing-arts", "Performing Arts", "culture", 30),
    _category("culture-heritage-traditions", "Heritage & Traditions", "culture", 40),
    _category("everyday_knowledge", "Everyday Knowledge & Life Skills", order=110),
    _category("everyday_knowledge-health-safety", "Health & Safety", "everyday_knowledge", 10),
    _category("everyday_knowledge-food-cooking", "Food & Cooking", "everyday_knowledge", 20),
    _category("everyday_knowledge-practical-skills", "Practical Life Skills", "everyday_knowledge", 30),
    _category("everyday_knowledge-money-consumers", "Money & Consumers", "everyday_knowledge", 40),
)


TAGS: tuple[TagDefinition, ...] = (
    TagDefinition("calculation", "Calculation"),
    TagDefinition("critical-thinking", "Critical Thinking"),
    TagDefinition("current-affairs", "Current Affairs"),
    TagDefinition("real-world-application", "Real-World Application"),
    TagDefinition("visual-observation", "Visual Observation"),
    TagDefinition("wordplay", "Wordplay"),
)


# Exact normalized legacy labels only. The generic label "general" is mapped
# only on reviewed built-in records; user-authored values remain untouched.
LEGACY_CATEGORY_SLUGS: dict[str, str] = {
    "technology": "technology",
    "science": "science",
    "nature": "nature",
    "general-knowledge": "general_knowledge",
    "geography": "general_knowledge-geography",
    "history": "general_knowledge-history-civics",
    "space": "science-space-astronomy",
    "literature": "culture-literature",
    "art": "culture-visual-arts",
}


def normalize_legacy_category(value: str) -> str:
    """Normalize only separators/case; never rewrite the stored legacy value."""
    return "-".join(value.strip().lower().replace("_", " ").split())


def legacy_category_slug(value: str | None, provenance: str | None = None) -> str | None:
    if not value or not value.strip():
        return None
    normalized = normalize_legacy_category(value)
    if normalized == "general":
        return "general_knowledge" if provenance == "built_in_reviewed" else None
    return LEGACY_CATEGORY_SLUGS.get(normalized)


def validate_category_definitions(categories: tuple[CategoryDefinition, ...] = CATEGORIES) -> None:
    """Reject duplicate IDs/slugs, missing parents, and cyclic definition graphs."""
    by_slug = {item.slug: item for item in categories}
    if len(by_slug) != len(categories):
        raise ValueError("Category slugs must be unique.")
    if len({item.id for item in categories}) != len(categories):
        raise ValueError("Category IDs must be unique.")
    for item in categories:
        if item.parent_slug is not None and item.parent_slug not in by_slug:
            raise ValueError(f"Category {item.slug!r} refers to an unknown parent.")
        visited = {item.slug}
        parent_slug = item.parent_slug
        while parent_slug is not None:
            if parent_slug in visited:
                raise ValueError("Category definitions cannot contain cycles or self-parenting.")
            visited.add(parent_slug)
            parent_slug = by_slug[parent_slug].parent_slug


validate_category_definitions()
