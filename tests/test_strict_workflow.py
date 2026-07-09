import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prompt_evolver.errors import ValidationError
from prompt_evolver.model import ModelConfig
from prompt_evolver.storage import load_json, write_json
from prompt_evolver.strict import (
    strict_blackbox_candidate,
    strict_finalize,
    strict_ingest_candidate,
    strict_init,
    strict_log_candidate,
    strict_train_candidate,
    strict_verify,
)


class FakeClient:
    def __init__(self, config: ModelConfig):
        self.config = config

    def generate(self, prompt_text: str) -> str:
        if self.config.model == "judge-model":
            return '{"binary_score": 1}'
        return '{"label":"billing"}'


class StrictWorkflowTests(unittest.TestCase):
    def test_strict_candidate_requires_ordered_steps(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prompt, variables, out_dir = self._write_inputs(root)
            strict_init(variables, prompt, out_dir, max_iterations=2)

            with patch("prompt_evolver.workflow.TargetModelClient", FakeClient):
                strict_train_candidate(
                    prompt,
                    out_dir,
                    "candidate_000",
                    ModelConfig("target-model"),
                    strategy="baseline",
                )

                with self.assertRaises(ValidationError):
                    strict_blackbox_candidate(
                        out_dir,
                        "candidate_000",
                        ModelConfig("target-model"),
                        ModelConfig("judge-model"),
                    )

            judgement, reviews = self._write_judgement_and_reviews(out_dir, "candidate_000")
            strict_ingest_candidate(judgement, out_dir, "candidate_000", reviews)
            with self.assertRaises(ValidationError):
                strict_log_candidate(out_dir, "candidate_000")

    def test_strict_full_path_logs_metrics_and_finalizes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prompt, variables, out_dir = self._write_inputs(root)
            strict_init(
                variables,
                prompt,
                out_dir,
                max_iterations=2,
                target_pass_rate=0.95,
                target_average_score_100=95.0,
            )

            with patch("prompt_evolver.workflow.TargetModelClient", FakeClient):
                strict_train_candidate(
                    prompt,
                    out_dir,
                    "candidate_000",
                    ModelConfig("target-model"),
                    strategy="baseline",
                )
                judgement, reviews = self._write_judgement_and_reviews(out_dir, "candidate_000")
                strict_ingest_candidate(judgement, out_dir, "candidate_000", reviews)
                strict_blackbox_candidate(
                    out_dir,
                    "candidate_000",
                    ModelConfig("target-model"),
                    ModelConfig("judge-model"),
                )
                strict_log_candidate(out_dir, "candidate_000")

            verify_report = strict_verify(out_dir)
            self.assertTrue(verify_report["valid"], verify_report["errors"])

            log_record = json.loads((out_dir / "optimization_log.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(log_record["candidate_id"], "candidate_000")
            self.assertEqual(log_record["metrics"]["pass_rate"], 1.0)
            self.assertEqual(log_record["hidden_eval"]["pass_rate"], 1.0)

            state = load_json(out_dir / "strict_state.json")
            hidden_eval = state["candidates"]["candidate_000"]["hidden_eval"]
            for forbidden in (
                "case_scores",
                "case_ids",
                "variables_file",
                "rendered_prompts",
                "target_outputs",
                "judge_prompts",
            ):
                self.assertNotIn(forbidden, hidden_eval)

            summary = strict_finalize(out_dir)
            self.assertEqual(summary["selected_candidate_id"], "candidate_000")
            self.assertEqual(summary["hidden_eval"]["pass_rate"], 1.0)
            self.assertTrue((out_dir / "final" / "best_prompt.md").exists())

    def test_strict_verify_rejects_incomplete_candidate_with_blackbox_artifact(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prompt, variables, out_dir = self._write_inputs(root)
            strict_init(variables, prompt, out_dir, max_iterations=2)

            with patch("prompt_evolver.workflow.TargetModelClient", FakeClient):
                strict_train_candidate(
                    prompt,
                    out_dir,
                    "candidate_000",
                    ModelConfig("target-model"),
                    strategy="baseline",
                )

            write_json(
                out_dir / "blackbox_score_candidate_000.json",
                {
                    "schema_version": "1.0",
                    "candidate_id": "candidate_000",
                    "case_count": 1,
                    "scored_count": 1,
                    "passed_count": 1,
                    "pass_rate": 1.0,
                },
            )
            report = strict_verify(out_dir)

            self.assertFalse(report["valid"])
            self.assertTrue(any("expected logged" in error for error in report["errors"]))
            with self.assertRaises(ValidationError):
                strict_finalize(out_dir)

    def _write_inputs(self, root: Path) -> tuple[Path, Path, Path]:
        prompt = root / "prompt.md"
        variables = root / "task.json"
        out_dir = root / "trace"
        prompt.write_text("Request: {{request}}", encoding="utf-8")
        variables.write_text(
            json.dumps(
                {
                    "task": {"rubric": "Return the right label."},
                    "cases": [
                        {
                            "id": "billing_case",
                            "variables": {"request": "refund"},
                            "expected": {"ground_truth": {"label": "billing"}},
                        },
                        {
                            "id": "technical_case",
                            "variables": {"request": "login"},
                            "expected": {"ground_truth": {"label": "technical"}},
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        return prompt, variables, out_dir

    def _write_judgement_and_reviews(self, out_dir: Path, candidate_id: str) -> tuple[Path, Path]:
        pack = load_json(out_dir / f"judge_pack_{candidate_id}.json")
        judgement = out_dir / f"external_judgement_{candidate_id}.json"
        reviews = out_dir / f"external_subagent_reviews_{candidate_id}.json"
        judgement.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "candidate_id": candidate_id,
                    "judge": "codex",
                    "case_judgements": [
                        {
                            "case_id": case["case_id"],
                            "binary_score": 1,
                            "score_100": 100,
                            "rationale": "Correct.",
                            "failure_tags": [],
                            "improvement_advice": "",
                        }
                        for case in pack["cases"]
                    ],
                }
            ),
            encoding="utf-8",
        )
        reviews.write_text(
            json.dumps({"schema_version": "1.0", "candidate_id": candidate_id, "reviews": []}),
            encoding="utf-8",
        )
        return judgement, reviews


if __name__ == "__main__":
    unittest.main()
