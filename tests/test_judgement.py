import unittest

from prompt_evolver.schemas import compute_metrics, judgement_cases


class JudgementTests(unittest.TestCase):
    def test_validates_required_scores_and_computes_metrics(self):
        judgement = {
            "candidate_id": "initial",
            "case_judgements": [
                {
                    "case_id": "a",
                    "binary_score": 1,
                    "score_100": 95,
                    "rationale": "Correct.",
                    "failure_tags": [],
                    "improvement_advice": "",
                },
                {
                    "case_id": "b",
                    "binary_score": 0,
                    "score_100": 40,
                    "rationale": "Wrong label.",
                    "failure_tags": ["wrong_label"],
                    "improvement_advice": "Define label boundaries.",
                },
            ],
        }
        self.assertEqual(len(judgement_cases(judgement)), 2)
        metrics = compute_metrics(judgement)
        self.assertEqual(metrics["case_count"], 2)
        self.assertEqual(metrics["passed_count"], 1)
        self.assertEqual(metrics["pass_rate"], 0.5)
        self.assertEqual(metrics["average_score_100"], 67.5)


if __name__ == "__main__":
    unittest.main()

