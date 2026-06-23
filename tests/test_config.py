import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_prompt_optimizer.cli import _model_config
from codex_prompt_optimizer.config import parse_env_line


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


if __name__ == "__main__":
    unittest.main()
