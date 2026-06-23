import json
import tempfile
import unittest
from pathlib import Path

from codex_prompt_optimizer.storage import write_jsonl
from codex_prompt_optimizer.workflow import (
    finalize_best,
    ingest_judgement,
    make_judge_pack,
    render_cases,
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
            workdir = root / ".prompt-opt"
            prompt_dir = workdir / "prompts"
            prompt_dir.mkdir(parents=True)
            candidate_prompt = prompt_dir / "initial.md"

            prompt.write_text("Request: {{request}}", encoding="utf-8")
            candidate_prompt.write_text("Request: {{request}}", encoding="utf-8")
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
            ingest_judgement(judgement, workdir, target_pass_rate=1.0)
            summary = finalize_best(workdir, root / "final", target_pass_rate=1.0)
            self.assertEqual(summary["best_candidate_id"], "initial")
            self.assertTrue((root / "final" / "best_prompt.md").exists())


if __name__ == "__main__":
    unittest.main()

