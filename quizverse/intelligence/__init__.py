"""AI Intelligence Engine package for QuizVerse Live."""

from quizverse.intelligence.adaptive_difficulty import AdaptiveDifficultyEngine, AdaptiveDifficultyResult
from quizverse.intelligence.answer_validation import AnswerValidationResult, AnswerValidator
from quizverse.intelligence.duplicate_detection import DuplicateCandidate, DuplicateCheckResult, DuplicateDetector
from quizverse.intelligence.intelligence_service import AIIntelligenceService
from quizverse.intelligence.learning_paths import GeneratedLearningPath, LearningPathGenerator, PathStepSpec
from quizverse.intelligence.learning_reports import LearningReportGenerator, LearningReportResult
from quizverse.intelligence.performance import PerformanceIntelligenceAnalyzer
from quizverse.intelligence.question_quality import QuestionQualityAnalyzer, QuestionQualityResult
from quizverse.intelligence.recommendations import QuizRecommendation, QuizRecommender
from quizverse.intelligence.weakness_detection import TopicAnalysis, WeaknessDetector, WeaknessSummary

__all__ = [
    "AIIntelligenceService",
    "AdaptiveDifficultyEngine",
    "AdaptiveDifficultyResult",
    "AnswerValidator",
    "AnswerValidationResult",
    "DuplicateCandidate",
    "DuplicateCheckResult",
    "DuplicateDetector",
    "GeneratedLearningPath",
    "LearningPathGenerator",
    "LearningReportGenerator",
    "LearningReportResult",
    "PathStepSpec",
    "PerformanceIntelligenceAnalyzer",
    "QuestionQualityAnalyzer",
    "QuestionQualityResult",
    "QuizRecommendation",
    "QuizRecommender",
    "TopicAnalysis",
    "WeaknessDetector",
    "WeaknessSummary",
]
