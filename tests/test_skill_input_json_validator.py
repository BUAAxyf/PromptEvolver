import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/prompt-evolver/scripts/validate_input_json.py"


class SkillInputJsonValidatorTests(unittest.TestCase):
    def test_validates_json_shape_and_prompt_variables(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            variables = root / "task.json"
            prompt = root / "prompt.md"
            variables.write_text(
                json.dumps(
                    {
                        "task": {"name": "Classifier"},
                        "globals": {"locale": "zh-CN"},
                        "cases": [
                            {
                                "id": "case_a",
                                "request": "refund please",
                                "expected": {"label": "billing"},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            prompt.write_text("{{locale}} {{request}}", encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(variables), "--prompt", str(prompt)],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["valid"])
        self.assertEqual(report["case_count"], 1)
        self.assertEqual(report["template_variables"], ["locale", "request"])
        self.assertEqual(report["missing_variables_by_case"], {})

    def test_rejects_duplicate_ids_and_missing_prompt_variables(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            variables = root / "task.json"
            prompt = root / "prompt.md"
            variables.write_text(
                json.dumps(
                    {
                        "cases": [
                            {"id": "dup", "variables": {"request": "a"}},
                            {"case_id": "dup", "variables": {"request": "b"}},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            prompt.write_text("{{request}} {{missing_value}}", encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(variables), "--prompt", str(prompt)],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertFalse(report["valid"])
        self.assertIn("duplicate case id: dup", report["errors"])
        self.assertEqual(
            report["missing_variables_by_case"],
            {"dup": ["missing_value"]},
        )


if __name__ == "__main__":
    unittest.main()
