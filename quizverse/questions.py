"""Small, reviewed general-knowledge bank used by the server engine."""

import hashlib
from dataclasses import dataclass

from quizverse.taxonomy import legacy_category_slug


@dataclass(frozen=True, slots=True)
class Question:
    category: str
    prompt: str
    options: tuple[str, str, str, str]
    answer: int
    id: str = ""
    category_id: str | None = None
    difficulty: str = "medium"
    language: str = "en"
    learning_levels: tuple[str, ...] = ()
    review_status: str = "reviewed"
    question_format: str = "multiple_choice"
    exam_collection_ids: frozenset[str] = frozenset()
    provenance: str = "built_in_reviewed"
    format_payload: dict[str, object] | None = None
    explanation: str | None = None

    def __post_init__(self) -> None:
        if not self.id:
            object.__setattr__(self, "id", hashlib.sha256(self.prompt.encode("utf-8")).hexdigest())
        if self.category_id is None:
            slug = legacy_category_slug(self.category, self.provenance)
            if slug:
                object.__setattr__(self, "category_id", f"category:{slug}")


QUESTIONS: tuple[Question, ...] = (
    Question("Science", "Which planet is known as the Red Planet?", ("Venus", "Mars", "Jupiter", "Mercury"), 1, explanation="Mars is known as the Red Planet because iron minerals in its soil oxidize, or rust, giving it a reddish appearance."),
    Question("Geography", "What is the capital city of Australia?", ("Sydney", "Perth", "Canberra", "Melbourne"), 2, explanation="Canberra was selected as the federal capital of Australia in 1908 as a compromise between Sydney and Melbourne."),
    Question("Technology", "What does CPU stand for?", ("Central Processing Unit", "Computer Power Utility", "Core Program Unit", "Central Performance User"), 0, explanation="CPU stands for Central Processing Unit, the primary component that executes computer program instructions."),
    Question("Nature", "Which is the largest mammal on Earth?", ("African elephant", "Blue whale", "Giraffe", "Orca"), 1, explanation="The blue whale is the largest known mammal and animal ever to have lived on Earth, reaching lengths of up to 30 meters."),
    Question("Space", "Which planet is famous for its prominent rings?", ("Neptune", "Earth", "Saturn", "Mars"), 2, explanation="Saturn has the most extensive and visually prominent planetary ring system in the Solar System, composed primarily of water ice with a trace of rocky material."),
    Question("Literature", "Who wrote the play Romeo and Juliet?", ("Charles Dickens", "Jane Austen", "William Shakespeare", "Mark Twain"), 2),
    Question("Science", "What is the chemical symbol for gold?", ("Ag", "Gd", "Go", "Au"), 3),
    Question("Geography", "Which is the largest ocean on Earth?", ("Atlantic Ocean", "Indian Ocean", "Pacific Ocean", "Arctic Ocean"), 2),
    Question("History", "The ancient Olympic Games originated in which country?", ("Italy", "Greece", "Egypt", "Turkey"), 1),
    Question("Art", "How many keys are there on a standard modern piano?", ("66", "72", "88", "92"), 2),
    Question("Science", "Which gas do plants absorb from the atmosphere?", ("Oxygen", "Nitrogen", "Carbon dioxide", "Hydrogen"), 2),
    Question("General", "How many sides does a hexagon have?", ("Five", "Six", "Seven", "Eight"), 1),
    Question("Technology", "Which language is primarily used to style web pages?", ("HTML", "CSS", "SQL", "Python"), 1),
    Question("Geography", "Which country is home to the city of Kyoto?", ("China", "South Korea", "Japan", "Thailand"), 2),
    Question("Nature", "What is the fastest land animal?", ("Lion", "Cheetah", "Pronghorn", "Horse"), 1),
    Question("Science", "What is the boiling point of water at sea level?", ("90°C", "100°C", "110°C", "120°C"), 1),
    Question("General", "How many colors are traditionally named in a rainbow?", ("Five", "Six", "Seven", "Eight"), 2),
    Question("History", "Which civilization built Machu Picchu?", ("Maya", "Aztec", "Inca", "Roman"), 2),
    Question("Technology", "What does URL stand for?", ("Universal Reference Link", "Uniform Resource Locator", "Unified Routing Language", "User Resource List"), 1),
    Question("Science", "Which organ pumps blood around the human body?", ("Liver", "Lung", "Brain", "Heart"), 3),
)
