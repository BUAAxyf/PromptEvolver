from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .errors import ModelExecutionError


@dataclass(frozen=True)
class ModelConfig:
    model: str
    api_base: str | None = None
    api_key_env: str = "DSPY_API_KEY"
    temperature: float | None = None
    max_tokens: int | None = None
    timeout_seconds: float | None = None
    enable_thinking: bool | None = None


class DspyTargetModel:
    def __init__(self, config: ModelConfig):
        self.config = config
        self._lm: Any | None = None

    def generate(self, prompt: str) -> str:
        lm = self._get_lm()
        try:
            result = lm(prompt)
        except Exception as exc:  # pragma: no cover - depends on external model service
            raise ModelExecutionError(f"DSPy target model call failed: {exc}") from exc
        return _normalize_model_result(result)

    def _get_lm(self) -> Any:
        if self._lm is not None:
            return self._lm
        try:
            import dspy  # type: ignore
        except ImportError as exc:  # pragma: no cover - dependency error path
            raise ModelExecutionError("dspy-ai is required for target model execution") from exc

        kwargs: dict[str, Any] = {}
        if self.config.api_base:
            kwargs["api_base"] = self.config.api_base
        api_key = os.environ.get(self.config.api_key_env)
        if api_key:
            kwargs["api_key"] = api_key
        if self.config.temperature is not None:
            kwargs["temperature"] = self.config.temperature
        if self.config.max_tokens is not None:
            kwargs["max_tokens"] = self.config.max_tokens
        if self.config.timeout_seconds is not None:
            kwargs["timeout"] = self.config.timeout_seconds
        if self.config.enable_thinking is not None:
            kwargs["extra_body"] = {"enable_thinking": self.config.enable_thinking}

        try:
            self._lm = dspy.LM(self.config.model, **kwargs)
        except Exception as exc:  # pragma: no cover - dependency/API compatibility path
            raise ModelExecutionError(f"failed to initialize dspy.LM: {exc}") from exc
        return self._lm


def _normalize_model_result(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        return "\n".join(_normalize_model_result(item) for item in result)
    if isinstance(result, dict):
        for key in ("text", "content", "output"):
            value = result.get(key)
            if isinstance(value, str):
                return value
    for attr in ("text", "content", "output"):
        value = getattr(result, attr, None)
        if isinstance(value, str):
            return value
    return str(result)
