class PromptOptimizerError(Exception):
    """Base exception for user-facing CLI errors."""


class ValidationError(PromptOptimizerError):
    """Raised when input files or structured artifacts are invalid."""


class ModelExecutionError(PromptOptimizerError):
    """Raised when the target model call fails."""

