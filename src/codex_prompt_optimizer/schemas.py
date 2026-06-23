from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .storage import load_json

RESERVED_CASE_KEYS = {
    "id",
    "case_id",
    "variables",
    "expected",
    "expected_output",
    "rubric",
    "metadata",
    "notes",
}


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    variables: dict[str, Any]
    expected: Any | None = None
    rubric: Any | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class TaskBundle:
    task: dict[str, Any]
    globals: dict[str, Any]
    cases: list[EvaluationCase]


def normalize_variables_file(path: Path) -> TaskBundle:
    data = load_json(path)
    if isinstance(data, list):
        raw_cases = data
        task: dict[str, Any] = {}
        globals_: dict[str, Any] = {}
    elif isinstance(data, dict):
        raw_cases = data.get("cases") or data.get("examples") or data.get("evaluation_cases")
        if raw_cases is None:
            raise ValidationError(
                "variables JSON must contain a 'cases', 'examples', or 'evaluation_cases' array"
            )
        task_value = data.get("task", {})
        if not isinstance(task_value, dict):
            raise ValidationError("'task' must be an object when provided")
        task = task_value
        globals_value = data.get("globals", {})
        if not isinstance(globals_value, dict):
            raise ValidationError("'globals' must be an object when provided")
        globals_ = globals_value
    else:
        raise ValidationError("variables JSON must be an object or an array of cases")

    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValidationError("variables JSON must contain at least one case")

    seen_ids: set[str] = set()
    cases: list[EvaluationCase] = []
    for index, raw_case in enumerate(raw_cases, start=1):
        if not isinstance(raw_case, dict):
            raise ValidationError(f"case #{index} must be an object")
        case_id = str(raw_case.get("id") or raw_case.get("case_id") or f"case_{index:03d}")
        if case_id in seen_ids:
            raise ValidationError(f"duplicate case id: {case_id}")
        seen_ids.add(case_id)

        variables_value = raw_case.get("variables")
        if variables_value is None:
            variables_value = {
                key: value for key, value in raw_case.items() if key not in RESERVED_CASE_KEYS
            }
        if not isinstance(variables_value, dict):
            raise ValidationError(f"case {case_id}: variables must be an object")

        variables = {**globals_, **variables_value}
        metadata_value = raw_case.get("metadata")
        if metadata_value is not None and not isinstance(metadata_value, dict):
            raise ValidationError(f"case {case_id}: metadata must be an object when provided")

        cases.append(
            EvaluationCase(
                case_id=case_id,
                variables=variables,
                expected=raw_case.get("expected", raw_case.get("expected_output")),
                rubric=raw_case.get("rubric"),
                metadata=metadata_value,
            )
        )

    return TaskBundle(task=task, globals=globals_, cases=cases)


def judgement_cases(judgement: dict[str, Any]) -> list[dict[str, Any]]:
    cases = judgement.get("case_judgements")
    if not isinstance(cases, list) or not cases:
        raise ValidationError("judgement must contain non-empty 'case_judgements'")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(cases, start=1):
        if not isinstance(item, dict):
            raise ValidationError(f"case_judgements[{index}] must be an object")
        case_id = item.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValidationError(f"case_judgements[{index}] must include case_id")
        if case_id in seen:
            raise ValidationError(f"duplicate judgement case id: {case_id}")
        seen.add(case_id)
        binary_score = item.get("binary_score")
        if binary_score not in (0, 1):
            raise ValidationError(f"case {case_id}: binary_score must be 0 or 1")
        score_100 = item.get("score_100")
        if not isinstance(score_100, int) or not 0 <= score_100 <= 100:
            raise ValidationError(f"case {case_id}: score_100 must be an integer from 0 to 100")
        failure_tags = item.get("failure_tags", [])
        if not isinstance(failure_tags, list) or not all(
            isinstance(tag, str) for tag in failure_tags
        ):
            raise ValidationError(f"case {case_id}: failure_tags must be an array of strings")
        for key in ("rationale", "improvement_advice"):
            if key in item and item[key] is not None and not isinstance(item[key], str):
                raise ValidationError(f"case {case_id}: {key} must be a string")
        normalized.append(item)
    return normalized


def compute_metrics(judgement: dict[str, Any]) -> dict[str, Any]:
    cases = judgement_cases(judgement)
    total = len(cases)
    passed = sum(1 for case in cases if case["binary_score"] == 1)
    average_score = sum(case["score_100"] for case in cases) / total
    return {
        "case_count": total,
        "passed_count": passed,
        "pass_rate": passed / total,
        "average_score_100": average_score,
    }

