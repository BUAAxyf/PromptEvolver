import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prompt_evolver.cli import _evaluator_model_config, _model_config
from prompt_evolver.config import (
    init_model_config_file,
    model_config_status,
    parse_env_line,
    read_env_file,
    set_model_config_value,
)
from prompt_evolver.model import runtime_model_name


class ConfigTests(unittest.TestCase):
    def test_parse_env_line_handles_quotes_and_comments(self):
        self.assertEqual(parse_env_line("MODEL_API_KEY='abc123'"), ("MODEL_API_KEY", "abc123"))
        self.assertEqual(parse_env_line("export MODEL_MAX_TOKENS=2048"), ("MODEL_MAX_TOKENS", "2048"))
        self.assertEqual(parse_env_line("MODEL_NAME=model-name # local"), ("MODEL_NAME", "model-name"))
        self.assertIsNone(parse_env_line("# comment"))

    def test_model_config_loads_dotenv_defaults(self):
        old_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".env").write_text(
                "\n".join(
                    [
                        "MODEL_NAME=test-model",
                        "MODEL_API_BASE=https://example.invalid/v1",
                        "MODEL_API_KEY=secret",
                        "MODEL_TEMPERATURE=0.1",
                        "MODEL_MAX_TOKENS=2048",
                        "MODEL_TIMEOUT_SECONDS=90",
                        "MODEL_ENABLE_THINKING=true",
                    ]
                ),
                encoding="utf-8",
            )
            try:
                os.chdir(root)
                with patch.dict(os.environ, {}, clear=True):
                    config = _model_config(None, None, "MODEL_API_KEY", None, None, None, None)
            finally:
                os.chdir(old_cwd)

        self.assertEqual(config.model, "test-model")
        self.assertEqual(config.api_base, "https://example.invalid/v1")
        self.assertEqual(config.api_key_env, "MODEL_API_KEY")
        self.assertEqual(config.temperature, 0.1)
        self.assertEqual(config.max_tokens, 2048)
        self.assertEqual(config.timeout_seconds, 90)
        self.assertTrue(config.enable_thinking)

    def test_evaluator_model_config_falls_back_to_target_model(self):
        old_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".env").write_text(
                "\n".join(
                    [
                        "MODEL_NAME=test-model",
                        "MODEL_API_BASE=https://example.invalid/v1",
                        "MODEL_API_KEY=secret",
                        "MODEL_TEMPERATURE=0.2",
                        "MODEL_MAX_TOKENS=1024",
                        "MODEL_TIMEOUT_SECONDS=45",
                        "MODEL_ENABLE_THINKING=false",
                        "EVALUATOR_MODEL_NAME=",
                        "EVALUATOR_MODEL_API_BASE=",
                        "EVALUATOR_MODEL_API_KEY=",
                    ]
                ),
                encoding="utf-8",
            )
            try:
                os.chdir(root)
                with patch.dict(os.environ, {}, clear=True):
                    config = _evaluator_model_config(
                        None,
                        None,
                        "EVALUATOR_MODEL_API_KEY",
                        None,
                        None,
                        None,
                        None,
                    )
            finally:
                os.chdir(old_cwd)

        self.assertEqual(config.model, "test-model")
        self.assertEqual(config.api_base, "https://example.invalid/v1")
        self.assertEqual(config.api_key_env, "MODEL_API_KEY")
        self.assertEqual(config.temperature, 0.2)
        self.assertEqual(config.max_tokens, 1024)
        self.assertEqual(config.timeout_seconds, 45)
        self.assertFalse(config.enable_thinking)

    def test_runtime_model_name_prefixes_openai_compatible_models(self):
        self.assertEqual(runtime_model_name("DeepSeek-V4-Pro", "https://example.invalid/v1"), "openai/DeepSeek-V4-Pro")
        self.assertEqual(runtime_model_name("openai/gpt-4o", "https://example.invalid/v1"), "openai/gpt-4o")

    def test_init_and_set_model_config_file(self):
        with tempfile.TemporaryDirectory() as temp:
            env_file = Path(temp) / ".env"
            self.assertTrue(init_model_config_file(env_file))
            self.assertFalse(init_model_config_file(env_file))

            set_model_config_value(env_file, "MODEL_NAME", "DeepSeek-V4-Pro")
            set_model_config_value(env_file, "MODEL_API_KEY", "secret-value")
            set_model_config_value(env_file, "MODEL_MAX_TOKENS", "1024")

            values = read_env_file(env_file)
            self.assertEqual(values["MODEL_NAME"], "DeepSeek-V4-Pro")
            self.assertEqual(values["MODEL_API_KEY"], "secret-value")
            self.assertEqual(values["MODEL_MAX_TOKENS"], "1024")
            self.assertIn("EVALUATOR_MODEL_NAME", values)

            status = model_config_status(env_file)
            self.assertEqual(status["values"]["MODEL_API_KEY"], "secr...alue")
            self.assertEqual(status["missing_required"], [])
            self.assertEqual(status["evaluator_fallbacks"]["EVALUATOR_MODEL_NAME"], "MODEL_NAME")
            self.assertEqual(status["evaluator_fallbacks"]["EVALUATOR_MODEL_API_KEY"], "MODEL_API_KEY")

            set_model_config_value(env_file, "EVALUATOR_MODEL_API_KEY", "judge-secret")
            status = model_config_status(env_file)
            self.assertEqual(status["values"]["EVALUATOR_MODEL_API_KEY"], "judg...cret")

    def test_set_rejects_invalid_model_config(self):
        with tempfile.TemporaryDirectory() as temp:
            env_file = Path(temp) / ".env"
            with self.assertRaises(ValueError):
                set_model_config_value(env_file, "UNKNOWN", "value")
            with self.assertRaises(ValueError):
                set_model_config_value(env_file, "MODEL_MAX_TOKENS", "0")
            with self.assertRaises(ValueError):
                set_model_config_value(env_file, "MODEL_ENABLE_THINKING", "maybe")
            with self.assertRaises(ValueError):
                set_model_config_value(env_file, "EVALUATOR_MODEL_MAX_TOKENS", "0")


if __name__ == "__main__":
    unittest.main()
