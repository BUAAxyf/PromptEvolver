class PromptEvolverError(Exception):
    """Base exception for user-facing CLI errors."""


class ValidationError(PromptEvolverError):
    """Raised when input files or structured artifacts are invalid."""


class ModelExecutionError(PromptEvolverError):
    """Raised when the target model call fails."""
