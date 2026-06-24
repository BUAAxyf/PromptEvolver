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

    def test_skill_reference_documents_input_json_format(self):
        root = Path(__file__).resolve().parents[1]
        skill = root / "skills/codex-prompt-optimizer/SKILL.md"
        reference = root / "skills/codex-prompt-optimizer/references/input-json-format.md"
        skill_text = skill.read_text(encoding="utf-8")
        reference_text = reference.read_text(encoding="utf-8")

        self.assertIn("references/input-json-format.md", skill_text)
        self.assertIn("scripts/validate_input_json.py", skill_text)
        self.assertIn("scripts/validate_input_json.py", reference_text)
        for required_text in (
            "| `task` | object | No |",
            "| `globals` | object | No |",
            "| `cases` | array<object> | Conditionally yes |",
            "| `variables` | object | No |",
            "Reserved case fields are",
            "Effective render variables are `globals` merged with case variables",
        ):
            self.assertIn(required_text, reference_text)


if __name__ == "__main__":
    unittest.main()
