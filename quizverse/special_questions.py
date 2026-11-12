"""Small hand-authored and checked inventory for the two structured modes."""

from __future__ import annotations

from quizverse.questions import Question

_LEVELS = (
    "teen_challenge", "college_arena", "expert_arena",
)


def _make_open_questions() -> tuple[Question, ...]:
    rows = (
        ("Which gas do green plants take in from the air during photosynthesis?", "carbon dioxide", ("CO2", "carbon dioxide gas"), "science-biology"),
        ("What is the name of the force that pulls objects toward Earth?", "gravity", ("gravitational force",), "science-physics"),
        ("What is the largest planet in our solar system?", "Jupiter", ("the planet Jupiter",), "science-space-astronomy"),
        ("What is the process called when liquid water becomes water vapor?", "evaporation", ("vaporization",), "science-physics"),
        ("Which part of a plant usually absorbs water from the soil?", "roots", ("the roots", "root"), "science-biology"),
        ("What is the capital city of Japan?", "Tokyo", ("Tokyo, Japan",), "general_knowledge-geography"),
        ("How many sides does a hexagon have?", "six", ("6",), "mathematics-geometry"),
        ("What is the name of a word that has the opposite meaning of another word?", "antonym", ("an antonym",), "verbal-vocabulary"),
        ("Which instrument measures temperature?", "thermometer", ("a thermometer",), "science-physics"),
        ("What is the imaginary line around Earth's middle called?", "the equator", ("equator",), "general_knowledge-geography"),
    )
    output = []
    for index, (prompt, answer, aliases, category) in enumerate(rows, start=1):
        output.append(Question(
            category="General Knowledge", prompt=prompt, options=("", "", "", ""), answer=-1,
            id=f"bluff-{index:02d}", category_id=f"category:{category}", difficulty="medium",
            language="en", learning_levels=_LEVELS, review_status="reviewed",
            question_format="open_ended", format_payload={
                "canonical_answer": answer, "accepted_aliases": list(aliases),
            },
        ))
    return tuple(output)


BLUFF_QUESTIONS = _make_open_questions()

_PUZZLE_ROWS = (
    ("What number comes next: 2, 4, 6, 8, __?", "10", ("add two each time",), "even numbers"),
    ("A key is in the box marked 1. Boxes 2 and 3 are empty. Which box should you open?", "1", ("box 1", "box one"), "Read the clue carefully"),
    ("Unscramble the letters P A R to name a fruit.", "pear", (), "The word has four letters; one letter is missing from the visible set"),
    ("Which is heavier: one kilogram of cotton or one kilogram of iron?", "equal", ("they weigh the same", "same weight"), "Compare the stated masses"),
    ("If today is Monday, what day is two days later?", "Wednesday", (), "Count forward one day at a time"),
    ("Complete the pattern: red, blue, red, blue, __?", "red", (), "The two colors alternate"),
    ("A room has four corners. One corner is hidden behind a screen. How many corners does the room have?", "four", ("4",), "A screen does not change the room"),
    ("What is the smallest whole number greater than 9 that is divisible by 5?", "10", ("ten",), "Multiples of five end in 0 or 5"),
    ("Put these months in calendar order: March, January, February. Which comes second?", "February", (), "Start with January"),
    ("A code doubles 3, then adds 1. What is the result?", "7", ("seven",), "Apply the steps in order"),
)

ESCAPE_QUESTIONS = tuple(
    Question(
        category="Logic Puzzles", prompt=prompt, options=("", "", "", ""), answer=-1,
        id=f"escape-starter-{index:02d}", category_id="category:reasoning-logic-puzzles",
        difficulty="medium", language="en", learning_levels=_LEVELS,
        review_status="reviewed", question_format="puzzle",
        format_payload={
            "puzzle_type": "escape-room-riddle", "solution": solution, "accepted_aliases": list(aliases),
            "puzzle_set_id": "quizverse-starter-escape", "room_index": (index - 1) // 2 + 1,
            "puzzle_index": index - 1,
            "dependencies": [f"escape-starter-{index - 1:02d}"] if index > 1 else [],
            "hint": hint, "room_metadata": {"room_title": f"Room {(index - 1) // 2 + 1}"},
        },
    )
    for index, (prompt, solution, aliases, hint) in enumerate(_PUZZLE_ROWS, start=1)
)
