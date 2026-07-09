import json
import tempfile
import unittest
from pathlib import Path

from typer.testing import CliRunner

from prompt_evolver.cli import app


class CliContractTests(unittest.TestCase):
    def test_cli_does_not_expose_prompt_generation_command(self):
        result = CliRunner().invoke(app, ["--help"])

        self.assertEqual(result.exit_code, 0)
        self.assertNotIn("propose", result.output)
        self.assertIn("optimize-step", result.output)
        self.assertIn("split", result.output)
        self.assertIn("test-step", result.output)
        self.assertIn("blackbox-eval", result.output)
        self.assertIn("score-accuracy", result.output)
        self.assertIn("prompt-diff", result.output)
        self.assertIn("strict", result.output)

    def test_strict_cli_exposes_state_machine_commands(self):
        result = CliRunner().invoke(app, ["strict", "--help"])

        self.assertEqual(result.exit_code, 0)
        for command in (
            "init",
            "train-candidate",
            "ingest-candidate",
            "blackbox-candidate",
            "log-candidate",
            "verify",
            "finalize",
        ):
            self.assertIn(command, result.output)

    def test_skill_reference_contains_subagent_guardrails(self):
        root = Path(__file__).resolve().parents[1]
        prompt = root / "skills/prompt-evolver/references/judge-subagent-prompt.md"
        text = prompt.read_text(encoding="utf-8")

        self.assertIn("不要增加badcase", text)
        self.assertIn("Do not ask for files, links, paths", text)
        self.assertIn("dimension_scores", text)

    def test_split_command_writes_default_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            variables = root / "task.json"
            train = root / "train.json"
            test = root / "test.json"
            variables.write_text(
                json.dumps(
                    {
                        "cases": [
                            {
                                "id": f"case_{index}",
                                "variables": {"request": f"request {index}"},
                                "expected": {
                                    "ground_truth": {
                                        "label": "billing" if index < 2 else "technical"
                                    }
                                },
                            }
                            for index in range(4)
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = CliRunner().invoke(
                app,
                [
                    "split",
                    str(variables),
                    "--train-out",
                    str(train),
                    "--test-out",
                    str(test),
                ],
            )

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertTrue(train.exists())
            self.assertTrue(test.exists())
            self.assertEqual(len(json.loads(train.read_text(encoding="utf-8"))["cases"]), 2)
            self.assertEqual(len(json.loads(test.read_text(encoding="utf-8"))["cases"]), 2)

    def test_skill_reference_documents_input_json_format(self):
        root = Path(__file__).resolve().parents[1]
        skill = root / "skills/prompt-evolver/SKILL.md"
        reference = root / "skills/prompt-evolver/references/input-json-format.md"
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
            "Train/Hidden Evaluation Split And Accuracy Fields",
            "expected.ground_truth",
            "blackbox-eval",
        ):
            self.assertIn(required_text, reference_text)


if __name__ == "__main__":
    unittest.main()
