from __future__ import annotations

import os
from pathlib import Path
from typing import Any

MODEL_CONFIG_FIELDS: tuple[str, ...] = (
    "DSPY_MODEL",
    "DSPY_API_BASE",
    "DSPY_API_KEY",
    "DSPY__TEMPERATURE",
    "DSPY__MAX_TOKENS",
    "DSPY__TIMEOUT_SECONDS",
    "EVO_EVAL_ENABLE_THINKING",
)

SECRET_FIELDS = {"DSPY_API_KEY"}

DEFAULT_MODEL_CONFIG: dict[str, str] = {
    "DSPY_MODEL": "",
    "DSPY_API_BASE": "",
    "DSPY_API_KEY": "",
    "DSPY__TEMPERATURE": "0.1",
    "DSPY__MAX_TOKENS": "2048",
    "DSPY__TIMEOUT_SECONDS": "90",
    "EVO_EVAL_ENABLE_THINKING": "true",
}


def load_dotenv_file(path: Path | str = ".env", override: bool = False) -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        parsed = parse_env_line(raw_line)
        if parsed is None:
            continue
        key, value = parsed
        if override or key not in os.environ:
            os.environ[key] = value


def read_env_file(path: Path | str = ".env") -> dict[str, str]:
    env_path = Path(path)
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        parsed = parse_env_line(raw_line)
        if parsed is not None:
            key, value = parsed
            values[key] = value
    return values


def init_model_config_file(path: Path | str = ".env", force: bool = False) -> bool:
    env_path = Path(path)
    if env_path.exists() and not force:
        return False
    lines = [
        "# Local model configuration. This file is ignored by Git.",
        *[f"{key}={value}" for key, value in DEFAULT_MODEL_CONFIG.items()],
    ]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def set_model_config_value(path: Path | str, key: str, value: str) -> None:
    validate_model_config_value(key, value)
    env_path = Path(path)
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    output: list[str] = []
    updated = False
    for line in lines:
        parsed = parse_env_line(line)
        if parsed is None:
            output.append(line)
            continue
        current_key, _ = parsed
        if current_key == key:
            output.append(f"{key}={format_env_value(value)}")
            updated = True
        else:
            output.append(line)
    if not updated:
        if output and output[-1].strip():
            output.append("")
        output.append(f"{key}={format_env_value(value)}")
    env_path.write_text("\n".join(output) + "\n", encoding="utf-8")


def model_config_status(path: Path | str = ".env", reveal_secrets: bool = False) -> dict[str, Any]:
    env_path = Path(path)
    file_values = read_env_file(env_path)
    effective_values = {**file_values}
    for key in MODEL_CONFIG_FIELDS:
        if key in os.environ:
            effective_values[key] = os.environ[key]
    values = {
        key: redact_value(effective_values.get(key, ""), key, reveal_secrets=reveal_secrets)
        for key in MODEL_CONFIG_FIELDS
    }
    missing_required = [
        key for key in ("DSPY_MODEL",) if not effective_values.get(key)
    ]
    missing_recommended = [
        key for key in ("DSPY_API_KEY",) if not effective_values.get(key)
    ]
    return {
        "env_file": str(env_path),
        "env_file_exists": env_path.exists(),
        "values": values,
        "missing_required": missing_required,
        "missing_recommended": missing_recommended,
        "next_steps": first_use_guidance(
            [*missing_required, *missing_recommended],
            env_path,
        )
        if missing_required or missing_recommended
        else [],
    }


def first_use_guidance(missing_keys: list[str], env_file: Path | str = ".env") -> list[str]:
    env_path = Path(env_file)
    steps: list[str] = []
    if not env_path.exists():
        steps.append(f"codex-prompt-opt config init --env-file {env_path}")
    for key in missing_keys:
        if key == "DSPY_MODEL":
            steps.append(f"codex-prompt-opt config set DSPY_MODEL <model-name> --env-file {env_path}")
        elif key == "DSPY_API_BASE":
            steps.append(f"codex-prompt-opt config set DSPY_API_BASE <api-base-url> --env-file {env_path}")
        elif key == "DSPY_API_KEY":
            steps.append(f"codex-prompt-opt config set DSPY_API_KEY <api-key> --env-file {env_path}")
    if not steps:
        steps.append("codex-prompt-opt config show")
    return steps


def parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].lstrip()
    if "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    if not key:
        return None
    return key, _clean_env_value(value.strip())


def validate_model_config_value(key: str, value: str) -> None:
    if key not in MODEL_CONFIG_FIELDS:
        allowed = ", ".join(MODEL_CONFIG_FIELDS)
        raise ValueError(f"unsupported model config key: {key}. Allowed keys: {allowed}")
    if key == "DSPY__TEMPERATURE":
        parsed = float(value)
        if parsed < 0:
            raise ValueError("DSPY__TEMPERATURE must be >= 0")
    elif key == "DSPY__MAX_TOKENS":
        parsed = int(value)
        if parsed <= 0:
            raise ValueError("DSPY__MAX_TOKENS must be > 0")
    elif key == "DSPY__TIMEOUT_SECONDS":
        parsed = float(value)
        if parsed <= 0:
            raise ValueError("DSPY__TIMEOUT_SECONDS must be > 0")
    elif key == "EVO_EVAL_ENABLE_THINKING":
        env_bool_value(value, key)


def env_float(name: str) -> float | None:
    value = os.environ.get(name)
    if value in (None, ""):
        return None
    return float(value)


def env_int(name: str) -> int | None:
    value = os.environ.get(name)
    if value in (None, ""):
        return None
    return int(value)


def env_bool(name: str) -> bool | None:
    value = os.environ.get(name)
    if value in (None, ""):
        return None
    return env_bool_value(value, name)


def env_bool_value(value: str, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def redact_value(value: str, key: str, reveal_secrets: bool = False) -> str:
    if not value:
        return ""
    if key not in SECRET_FIELDS or reveal_secrets:
        return value
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def format_env_value(value: str) -> str:
    if not value:
        return ""
    if any(char.isspace() for char in value) or "#" in value:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _clean_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    if " #" in value:
        return value.split(" #", 1)[0].rstrip()
    return value
