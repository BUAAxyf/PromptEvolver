import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prompt_evolver.cli import _model_config
from prompt_evolver.config import (
    init_model_config_file,
    model_config_status,
    parse_env_line,
    read_env_file,
    set_model_config_value,
)
from prompt_evolver.model import dspy_model_name


class ConfigTests(unittest.TestCase):
    def test_parse_env_line_handles_quotes_and_comments(self):
        self.assertEqual(parse_env_line("DSPY_API_KEY='abc123'"), ("DSPY_API_KEY", "abc123"))
        self.assertEqual(parse_env_line("export DSPY__MAX_TOKENS=2048"), ("DSPY__MAX_TOKENS", "2048"))
        self.assertEqual(parse_env_line("DSPY_MODEL=model-name # local"), ("DSPY_MODEL", "model-name"))
        self.assertIsNone(parse_env_line("# comment"))

    def test_model_config_loads_dotenv_defaults(self):
        old_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".env").write_text(
                "\n".join(
                    [
                        "DSPY_MODEL=test-model",
                        "DSPY_API_BASE=https://example.invalid/v1",
                        "DSPY_API_KEY=secret",
                        "DSPY__TEMPERATURE=0.1",
                        "DSPY__MAX_TOKENS=2048",
                        "DSPY__TIMEOUT_SECONDS=90",
                        "EVO_EVAL_ENABLE_THINKING=true",
                    ]
                ),
                encoding="utf-8",
            )
            try:
                os.chdir(root)
                with patch.dict(os.environ, {}, clear=True):
                    config = _model_config(None, None, "DSPY_API_KEY", None, None, None, None)
            finally:
                os.chdir(old_cwd)

        self.assertEqual(config.model, "test-model")
        self.assertEqual(config.api_base, "https://example.invalid/v1")
        self.assertEqual(config.api_key_env, "DSPY_API_KEY")
        self.assertEqual(config.temperature, 0.1)
        self.assertEqual(config.max_tokens, 2048)
        self.assertEqual(config.timeout_seconds, 90)
        self.assertTrue(config.enable_thinking)

    def test_dspy_model_name_prefixes_openai_compatible_models(self):
        self.assertEqual(dspy_model_name("DeepSeek-V4-Pro", "https://example.invalid/v1"), "openai/DeepSeek-V4-Pro")
        self.assertEqual(dspy_model_name("openai/gpt-4o", "https://example.invalid/v1"), "openai/gpt-4o")

    def test_init_and_set_model_config_file(self):
        with tempfile.TemporaryDirectory() as temp:
            env_file = Path(temp) / ".env"
            self.assertTrue(init_model_config_file(env_file))
            self.assertFalse(init_model_config_file(env_file))

            set_model_config_value(env_file, "DSPY_MODEL", "DeepSeek-V4-Pro")
            set_model_config_value(env_file, "DSPY_API_KEY", "secret-value")
            set_model_config_value(env_file, "DSPY__MAX_TOKENS", "1024")

            values = read_env_file(env_file)
            self.assertEqual(values["DSPY_MODEL"], "DeepSeek-V4-Pro")
            self.assertEqual(values["DSPY_API_KEY"], "secret-value")
            self.assertEqual(values["DSPY__MAX_TOKENS"], "1024")

            status = model_config_status(env_file)
            self.assertEqual(status["values"]["DSPY_API_KEY"], "secr...alue")
            self.assertEqual(status["missing_required"], [])

    def test_set_rejects_invalid_model_config(self):
        with tempfile.TemporaryDirectory() as temp:
            env_file = Path(temp) / ".env"
            with self.assertRaises(ValueError):
                set_model_config_value(env_file, "UNKNOWN", "value")
            with self.assertRaises(ValueError):
                set_model_config_value(env_file, "DSPY__MAX_TOKENS", "0")
            with self.assertRaises(ValueError):
                set_model_config_value(env_file, "EVO_EVAL_ENABLE_THINKING", "maybe")


if __name__ == "__main__":
    unittest.main()
