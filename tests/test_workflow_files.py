import json
import tempfile
import unittest
from pathlib import Path

from prompt_evolver.storage import write_jsonl
from prompt_evolver.workflow import (
    finalize_prompt,
    ingest_judgement,
    make_judge_pack,
    render_cases,
    score_accuracy,
    split_train_test,
)


class WorkflowFileTests(unittest.TestCase):
    def test_judge_pack_ingest_and_finalize(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prompt = root / "prompt.md"
            variables = root / "task.json"
            rendered = root / "rendered.jsonl"
            outputs = root / "outputs.jsonl"
            pack = root / "judge_pack.json"
            judgement = root / "judgement.json"
            enriched_judgements = root / "judgements"

            prompt.write_text("Request: {{request}}", encoding="utf-8")
            variables.write_text(
                json.dumps(
                    {
                        "task": {"rubric": "Return the right label."},
                        "cases": [
                            {
                                "id": "c1",
                                "variables": {"request": "refund"},
                                "expected": {"label": "billing"},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            render_cases(prompt, variables, rendered, "initial")
            write_jsonl(
                outputs,
                [
                    {
                        "candidate_id": "initial",
                        "case_id": "c1",
                        "output_text": '{"label":"billing"}',
                    }
                ],
            )
            pack_payload = make_judge_pack(rendered, outputs, variables, pack)
            self.assertEqual(pack_payload["candidate_id"], "initial")
            self.assertEqual(pack_payload["cases"][0]["target_output"], '{"label":"billing"}')
            self.assertFalse(pack_payload["codex_judge_contract"]["cli_generates_prompt"])
            self.assertEqual(
                pack_payload["codex_judge_contract"]["prompt_generation_owner"],
                "codex_master_agent",
            )

            judgement.write_text(
                json.dumps(
                    {
                        "candidate_id": "initial",
                        "judge": "codex",
                        "case_judgements": [
                            {
                                "case_id": "c1",
                                "binary_score": 1,
                                "score_100": 96,
                                "rationale": "Correct.",
                                "failure_tags": [],
                                "improvement_advice": "",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            ingest_result = ingest_judgement(
                judgement,
                out_dir=enriched_judgements,
                target_pass_rate=1.0,
            )
            self.assertEqual(ingest_result["metrics"]["passed_count"], 1)
            self.assertTrue((enriched_judgements / "judgement_initial.json").exists())

            summary = finalize_prompt(prompt, judgement, root / "final", target_pass_rate=1.0)
            self.assertEqual(summary["selected_candidate_id"], "initial")
            self.assertTrue((root / "final" / "best_prompt.md").exists())
            self.assertFalse((root / ".prompt-evolver" / "candidates.jsonl").exists())

    def test_split_train_test_stratifies_by_ground_truth(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            variables = root / "task.json"
            train = root / "train.json"
            test = root / "test.json"
            cases = []
            for label in ("billing", "technical"):
                for index in range(10):
                    cases.append(
                        {
                            "id": f"{label}_{index}",
                            "variables": {"request": f"{label} request {index}"},
                            "expected": {"ground_truth": {"label": label}},
                        }
                    )
            variables.write_text(json.dumps({"cases": cases}), encoding="utf-8")

            result = split_train_test(variables, train, test, train_ratio=0.7, seed=1)
            train_cases = json.loads(train.read_text(encoding="utf-8"))["cases"]
            test_cases = json.loads(test.read_text(encoding="utf-8"))["cases"]

        self.assertEqual(result["train_count"], 14)
        self.assertEqual(result["test_count"], 6)
        self.assertEqual({case["expected"]["ground_truth"]["label"] for case in train_cases}, {"billing", "technical"})
        self.assertEqual({case["expected"]["ground_truth"]["label"] for case in test_cases}, {"billing", "technical"})
        self.assertFalse({case["id"] for case in train_cases} & {case["id"] for case in test_cases})

    def test_score_accuracy_uses_acceptable_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            variables = root / "task.json"
            outputs = root / "outputs.jsonl"
            report = root / "accuracy.json"
            variables.write_text(
                json.dumps(
                    {
                        "cases": [
                            {
                                "id": "ok",
                                "variables": {"request": "refund"},
                                "expected": {
                                    "acceptable_outputs": [{"label": "billing"}],
                                },
                            },
                            {
                                "id": "bad",
                                "variables": {"request": "login"},
                                "expected": {
                                    "acceptable_outputs": [{"label": "technical"}],
                                },
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            write_jsonl(
                outputs,
                [
                    {"case_id": "ok", "output_text": '{"label":"billing","confidence":0.9}'},
                    {"case_id": "bad", "output_text": '{"label":"account"}'},
                ],
            )

            result = score_accuracy(variables, outputs, report)
            self.assertTrue(report.exists())

        self.assertEqual(result["case_count"], 2)
        self.assertEqual(result["correct_count"], 1)
        self.assertEqual(result["accuracy"], 0.5)


if __name__ == "__main__":
    unittest.main()
