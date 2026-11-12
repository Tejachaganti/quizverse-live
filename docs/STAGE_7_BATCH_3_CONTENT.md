# Stage 7 Batch 3 content inventory

Inventory was checked before the mode integration. The built-in bank contained 20 reviewed multiple-choice questions and no open-ended or puzzle records. The local `quizverse.db` contained 0 quiz questions and 0 quiz rows at that check. Counts in a deployed database may differ.

This batch adds two small, original, manually checked built-in inventories:

- **Bluff Master:** 10 English, medium-difficulty, reviewed open-ended questions. Each record has one canonical answer and zero or more normalized accepted aliases.
- **Escape Room:** one reviewed English puzzle set with exactly 10 medium-difficulty puzzles, arranged as 5 rooms with 2 puzzles per room. Each puzzle has a server-only solution, checked aliases where needed, one hint, room coordinates, and a dependency on the preceding puzzle.

These records are marked reviewed because their prompts, answer keys, aliases, hints, coordinates, and dependency chain were checked as part of this implementation. No AI-generated content is represented as reviewed. The starter inventory is English-only and tagged for Teen Challenge, College Arena, and Expert Arena. It does not match other language or suitability filters. Host category, language, difficulty, review, learning preset, topic-weight, and exam-collection filters remain hard constraints; a mismatch returns an inventory error instead of relaxing those constraints.

The built-in structured inventory is intentionally limited to 10 questions and one complete puzzle set. Public published open-ended and puzzle quizzes can be selected when their questions are reviewed and their format matches the mode. Private published quizzes require an owner or quiz-read capability. Structured quiz listings expose metadata only; canonical answers and solutions are loaded into the server-side game source.
