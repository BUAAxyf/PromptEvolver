import unittest
from pathlib import Path

from typer.testing import CliRunner

from codex_prompt_optimizer.cli import app


class CliContractTests(unittest.TestCase):
    def test_cli_does_not_expose_prompt_generation_command(self):
        result = CliRunner().invoke(app, ["--help"])

        self.assertEqual(result.exit_code, 0)
        self.assertNotIn("propose", result.output)
        self.assertIn("optimize-step", result.output)

    def test_skill_reference_contains_subagent_guardrails(self):
        root = Path(__file__).resolve().parents[1]
        prompt = root / "skills/codex-prompt-optimizer/references/judge-subagent-prompt.md"
        text = prompt.read_text(encoding="utf-8")

        self.assertIn("不要增加badcase", text)
        self.assertIn("Do not ask for files, links, paths", text)
        self.assertIn("dimension_scores", text)


if __name__ == "__main__":
    unittest.main()
