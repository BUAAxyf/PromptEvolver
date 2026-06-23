import json
import tempfile
import unittest
from pathlib import Path

from codex_prompt_optimizer.workflow import GUIDANCE_START, propose_prompt


class ProposeTests(unittest.TestCase):
    def test_propose_adds_gepa_lite_guidance_without_touching_variables(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prompt = root / "prompt.md"
            judgement = root / "judgement.json"
            out = root / "next.md"
            workdir = root / ".prompt-opt"

            prompt.write_text("Answer about {{topic}}.", encoding="utf-8")
            judgement.write_text(
                json.dumps(
                    {
                        "candidate_id": "initial",
                        "case_judgements": [
                            {
                                "case_id": "c1",
                                "binary_score": 0,
                                "score_100": 55,
                                "rationale": "Missing required field.",
                                "failure_tags": ["missing_field"],
                                "improvement_advice": "Explicitly require all fields.",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = propose_prompt(prompt, judgement, out, workdir=workdir, candidate_id="c2")

            next_prompt = out.read_text(encoding="utf-8")
            self.assertTrue(result["guidance_added"])
            self.assertIn(GUIDANCE_START, next_prompt)
            self.assertIn("Explicitly require all fields.", next_prompt)
            self.assertIn("{{topic}}", next_prompt)
            self.assertTrue((workdir / "candidates.jsonl").exists())


if __name__ == "__main__":
    unittest.main()

