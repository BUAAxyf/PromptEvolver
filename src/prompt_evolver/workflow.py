from __future__ import annotations

import json
import random
import shutil
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .model import ModelConfig, TargetModelClient
from .renderer import extract_mustache_variables, missing_variables, render_template
from .schemas import compute_metrics, normalize_variables_file
from .storage import (
    load_json,
    read_jsonl,
    sha256_text,
    utc_now_iso,
    write_json,
    write_jsonl,
)

SCHEMA_VERSION = "1.0"
CASE_LIST_FIELDS = ("cases", "examples", "evaluation_cases")


def validate_inputs(prompt_template: Path, variables_file: Path) -> dict[str, Any]:
    template = prompt_template.read_text(encoding="utf-8")
    bundle = normalize_variables_file(variables_file)
    required = extract_mustache_variables(template)
    missing_by_case: dict[str, list[str]] = {}
    for case in bundle.cases:
        missing = missing_variables(required, case.variables)
        if missing:
            missing_by_case[case.case_id] = missing
    return {
        "schema_version": SCHEMA_VERSION,
        "prompt_template": str(prompt_template),
        "variables_file": str(variables_file),
        "case_count": len(bundle.cases),
        "template_variables": sorted(required),
        "missing_variables_by_case": missing_by_case,
        "valid": not missing_by_case,
    }


def render_cases(
    prompt_template: Path,
    variables_file: Path,
    out: Path,
    candidate_id: str = "initial",
) -> list[dict[str, Any]]:
    validation = validate_inputs(prompt_template, variables_file)
    if not validation["valid"]:
        raise ValidationError(
            "missing template variables: "
            + json.dumps(validation["missing_variables_by_case"], ensure_ascii=False)
        )

    template = prompt_template.read_text(encoding="utf-8")
    bundle = normalize_variables_file(variables_file)
    template_hash = sha256_text(template)
    records: list[dict[str, Any]] = []
    for case in bundle.cases:
        rendered = render_template(template, case.variables)
        records.append(
            {
                "schema_version": SCHEMA_VERSION,
                "candidate_id": candidate_id,
                "case_id": case.case_id,
                "task_instance_terms": [
                    "rendered prompt",
                    "prompt instantiation",
                    "task instance",
                    "evaluation case",
                    "evaluation example",
                ],
                "prompt_template_sha256": template_hash,
                "variables": case.variables,
                "rendered_prompt": rendered,
            }
        )
    write_jsonl(out, records)
    return records


def run_target_model(
    rendered_cases: Path,
    out: Path,
    model_config: ModelConfig,
) -> list[dict[str, Any]]:
    client = TargetModelClient(model_config)
    records: list[dict[str, Any]] = []
    for rendered in read_jsonl(rendered_cases):
        output = client.generate(rendered["rendered_prompt"])
        records.append(
            {
                "schema_version": SCHEMA_VERSION,
                "candidate_id": rendered.get("candidate_id", "unknown"),
                "case_id": rendered["case_id"],
                "target_model": model_config.model,
                "model_settings": {
                    "api_base": model_config.api_base,
                    "api_key_env": model_config.api_key_env,
                    "temperature": model_config.temperature,
                    "max_tokens": model_config.max_tokens,
                    "timeout_seconds": model_config.timeout_seconds,
                    "enable_thinking": model_config.enable_thinking,
                },
                "created_at": utc_now_iso(),
                "output_text": output,
            }
        )
    write_jsonl(out, records)
    return records


def split_train_test(
    variables_file: Path,
    train_out: Path,
    test_out: Path,
    train_ratio: float = 0.7,
    seed: int = 13,
) -> dict[str, Any]:
    if not 0 < train_ratio < 1:
        raise ValidationError("train_ratio must be between 0 and 1")
    normalize_variables_file(variables_file)
    data = load_json(variables_file)
    raw_cases, case_field, root_is_array = _raw_case_list(data)
    if len(raw_cases) < 2:
        raise ValidationError("at least two cases are required to split train and test sets")

    train_indices, test_indices = _stratified_indices(raw_cases, train_ratio, seed)
    if not train_indices or not test_indices:
        raise ValidationError("split produced an empty train or test set")

    train_payload = _replace_case_list(data, case_field, root_is_array, [raw_cases[i] for i in train_indices])
    test_payload = _replace_case_list(data, case_field, root_is_array, [raw_cases[i] for i in test_indices])
    write_json(train_out, train_payload)
    write_json(test_out, test_payload)
    return {
        "schema_version": SCHEMA_VERSION,
        "variables_file": str(variables_file),
        "train_out": str(train_out),
        "test_out": str(test_out),
        "train_ratio": train_ratio,
        "test_ratio": round(1 - train_ratio, 10),
        "seed": seed,
        "stratify_by": "expected.ground_truth",
        "case_count": len(raw_cases),
        "train_count": len(train_indices),
        "test_count": len(test_indices),
    }


def score_accuracy(
    variables_file: Path,
    target_outputs: Path,
    out: Path | None = None,
) -> dict[str, Any]:
    bundle = normalize_variables_file(variables_file)
    outputs_by_case = {record["case_id"]: record for record in read_jsonl(target_outputs)}
    case_scores: list[dict[str, Any]] = []
    correct_count = 0
    parse_error_count = 0
    unscored_count = 0
    for case in bundle.cases:
        output_record = outputs_by_case.get(case.case_id)
        output_text = str(output_record.get("output_text", "")) if output_record else ""
        parsed, parse_error = _parse_json_output(output_text)
        expected_options = _expected_options(case.expected)
        passed = False
        if not output_record or not expected_options:
            unscored_count += 1
        elif parse_error is not None and any(isinstance(option, dict) for option in expected_options):
            parse_error_count += 1
        else:
            passed = any(_matches_expected(parsed if parse_error is None else output_text, option) for option in expected_options)
            if passed:
                correct_count += 1
        case_scores.append(
            {
                "case_id": case.case_id,
                "binary_score": 1 if passed else 0,
                "parse_error": parse_error,
                "scored": bool(output_record and expected_options),
            }
        )

    case_count = len(bundle.cases)
    scored_count = case_count - unscored_count
    report = {
        "schema_version": SCHEMA_VERSION,
        "variables_file": str(variables_file),
        "target_outputs": str(target_outputs),
        "case_count": case_count,
        "scored_count": scored_count,
        "correct_count": correct_count,
        "accuracy": correct_count / scored_count if scored_count else 0.0,
        "parse_error_count": parse_error_count,
        "unscored_count": unscored_count,
        "case_scores": case_scores,
    }
    if out is not None:
        write_json(out, report)
    return report


def test_step(
    prompt_template: Path,
    variables_file: Path,
    out_dir: Path,
    candidate_id: str,
    model_config: ModelConfig,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    rendered_path = out_dir / f"rendered_cases_{candidate_id}.jsonl"
    outputs_path = out_dir / f"target_outputs_{candidate_id}.jsonl"
    accuracy_path = out_dir / f"accuracy_{candidate_id}.json"
    render_cases(prompt_template, variables_file, rendered_path, candidate_id)
    run_target_model(rendered_path, outputs_path, model_config)
    report = score_accuracy(variables_file, outputs_path, accuracy_path)
    return {
        "candidate_id": candidate_id,
        "prompt_template": str(prompt_template),
        "test_variables_file": str(variables_file),
        "rendered_cases": str(rendered_path),
        "target_outputs": str(outputs_path),
        "accuracy_report": str(accuracy_path),
        "metrics": {
            "case_count": report["case_count"],
            "scored_count": report["scored_count"],
            "correct_count": report["correct_count"],
            "accuracy": report["accuracy"],
            "parse_error_count": report["parse_error_count"],
            "unscored_count": report["unscored_count"],
        },
    }


def make_judge_pack(
    rendered_cases: Path,
    target_outputs: Path,
    variables_file: Path,
    out: Path,
    target_pass_rate: float = 1.0,
    target_average_score_100: float = 90.0,
) -> dict[str, Any]:
    rendered_records = read_jsonl(rendered_cases)
    output_records = read_jsonl(target_outputs)
    bundle = normalize_variables_file(variables_file)

    outputs_by_case = {record["case_id"]: record for record in output_records}
    case_meta = {case.case_id: case for case in bundle.cases}
    cases: list[dict[str, Any]] = []
    candidate_ids = {record.get("candidate_id", "unknown") for record in rendered_records}
    for rendered in rendered_records:
        case_id = rendered["case_id"]
        output = outputs_by_case.get(case_id)
        if output is None:
            raise ValidationError(f"missing target output for case {case_id}")
        meta = case_meta.get(case_id)
        cases.append(
            {
                "case_id": case_id,
                "variables": rendered["variables"],
                "expected": meta.expected if meta else None,
                "rubric": meta.rubric if meta else None,
                "metadata": meta.metadata if meta else None,
                "rendered_prompt": rendered["rendered_prompt"],
                "target_output": output["output_text"],
                "judgement_template": {
                    "case_id": case_id,
                    "binary_score": 0,
                    "score_100": 0,
                    "rationale": "",
                    "failure_tags": [],
                    "improvement_advice": "",
                },
            }
        )

    pack = {
        "schema_version": SCHEMA_VERSION,
        "candidate_id": sorted(candidate_ids)[0] if len(candidate_ids) == 1 else "mixed",
        "task": bundle.task,
        "variables_file": str(variables_file),
        "success_criteria": {
            "target_pass_rate": target_pass_rate,
            "target_average_score_100": target_average_score_100,
        },
        "codex_judge_contract": {
            "codex_is_target_model_executor": False,
            "cli_calls_codex": False,
            "cli_generates_prompt": False,
            "prompt_generation_owner": "codex_master_agent",
            "case_review_owner": "codex_subagents",
            "required_output_file": "judgement.json",
            "required_scores": ["binary_score", "score_100"],
            "do_not_modify_variables_file": True,
            "do_not_add_badcases_to_prompt": True,
        },
        "cases": cases,
        "judgement_output_template": {
            "schema_version": SCHEMA_VERSION,
            "candidate_id": sorted(candidate_ids)[0] if len(candidate_ids) == 1 else "mixed",
            "judge": "codex",
            "case_judgements": [case["judgement_template"] for case in cases],
            "overall": {
                "summary": "",
                "meets_success_criteria": False,
            },
        },
    }
    write_json(out, pack)
    return pack


def optimize_step(
    prompt_template: Path,
    variables_file: Path,
    out_dir: Path,
    candidate_id: str,
    model_config: ModelConfig,
    target_pass_rate: float = 1.0,
    target_average_score_100: float = 90.0,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    rendered_path = out_dir / f"rendered_cases_{candidate_id}.jsonl"
    outputs_path = out_dir / f"target_outputs_{candidate_id}.jsonl"
    judge_pack_path = out_dir / f"judge_pack_{candidate_id}.json"
    render_cases(prompt_template, variables_file, rendered_path, candidate_id)
    run_target_model(rendered_path, outputs_path, model_config)
    make_judge_pack(
        rendered_path,
        outputs_path,
        variables_file,
        judge_pack_path,
        target_pass_rate=target_pass_rate,
        target_average_score_100=target_average_score_100,
    )
    return {
        "candidate_id": candidate_id,
        "prompt_template": str(prompt_template),
        "rendered_cases": str(rendered_path),
        "target_outputs": str(outputs_path),
        "judge_pack": str(judge_pack_path),
        "next_action": (
            "Codex master should dispatch judge subagents, aggregate judgement.json, "
            "and create the next prompt itself if another iteration is needed."
        ),
    }


def ingest_judgement(
    judgement_file: Path,
    out: Path | None = None,
    out_dir: Path | None = None,
    target_pass_rate: float = 1.0,
    target_average_score_100: float = 90.0,
) -> dict[str, Any]:
    judgement = load_json(judgement_file)
    if not isinstance(judgement, dict):
        raise ValidationError("judgement JSON must be an object")
    metrics = compute_metrics(judgement)
    meets = (
        metrics["pass_rate"] >= target_pass_rate
        and metrics["average_score_100"] >= target_average_score_100
    )
    candidate_id = str(judgement.get("candidate_id") or "unknown")
    judgement = {
        **judgement,
        "computed_metrics": metrics,
        "computed_success": {
            "target_pass_rate": target_pass_rate,
            "target_average_score_100": target_average_score_100,
            "meets_success_criteria": meets,
        },
    }
    stored_path: Path | None = out
    if stored_path is None and out_dir is not None:
        stored_path = out_dir / f"judgement_{candidate_id}.json"
    if stored_path is not None:
        write_json(stored_path, judgement)
    return {
        "candidate_id": candidate_id,
        "judgement_path": str(stored_path) if stored_path is not None else None,
        "metrics": metrics,
        "meets_success_criteria": meets,
    }


def finalize_prompt(
    prompt_template: Path,
    judgement_file: Path,
    out_dir: Path,
    target_pass_rate: float = 1.0,
    target_average_score_100: float = 90.0,
) -> dict[str, Any]:
    judgement = load_json(judgement_file)
    if not isinstance(judgement, dict):
        raise ValidationError("judgement JSON must be an object")
    metrics = compute_metrics(judgement)
    meets = (
        metrics["pass_rate"] >= target_pass_rate
        and metrics["average_score_100"] >= target_average_score_100
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    best_prompt_path = out_dir / "best_prompt.md"
    shutil.copyfile(prompt_template, best_prompt_path)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "selected_candidate_id": str(judgement.get("candidate_id") or prompt_template.stem),
        "best_prompt": str(best_prompt_path),
        "source_prompt": str(prompt_template),
        "judgement_file": str(judgement_file),
        "prompt_template_sha256": sha256_text(prompt_template.read_text(encoding="utf-8")),
        "metrics": metrics,
        "target_pass_rate": target_pass_rate,
        "target_average_score_100": target_average_score_100,
        "meets_success_criteria": meets,
    }
    write_json(out_dir / "summary.json", summary)
    write_run_report(out_dir / "run_report.md", summary)
    return summary


def write_run_report(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Prompt Optimization Report",
        "",
        f"- Selected candidate: `{summary['selected_candidate_id']}`",
        f"- Source prompt: `{summary['source_prompt']}`",
        f"- Judgement file: `{summary['judgement_file']}`",
        f"- Pass rate: {summary['metrics']['pass_rate']:.2%}",
        f"- Average score_100: {summary['metrics']['average_score_100']:.2f}",
        f"- Meets success criteria: {summary['meets_success_criteria']}",
        f"- Best prompt: `{summary['best_prompt']}`",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _raw_case_list(data: Any) -> tuple[list[dict[str, Any]], str, bool]:
    if isinstance(data, list):
        raw_cases = data
        case_field = "<root-array>"
        root_is_array = True
    elif isinstance(data, dict):
        present = [field for field in CASE_LIST_FIELDS if field in data]
        if not present:
            raise ValidationError("variables JSON must contain a case list before splitting")
        case_field = next((field for field in CASE_LIST_FIELDS if data.get(field)), present[0])
        raw_cases = data.get(case_field)
        root_is_array = False
    else:
        raise ValidationError("variables JSON must be an object or an array of cases")
    if not isinstance(raw_cases, list) or not all(isinstance(case, dict) for case in raw_cases):
        raise ValidationError("case list must be an array of objects")
    return raw_cases, case_field, root_is_array


def _replace_case_list(
    data: Any,
    case_field: str,
    root_is_array: bool,
    cases: list[dict[str, Any]],
) -> Any:
    if root_is_array:
        return cases
    output = dict(data)
    output[case_field] = cases
    return output


def _stratified_indices(
    raw_cases: list[dict[str, Any]],
    train_ratio: float,
    seed: int,
) -> tuple[list[int], list[int]]:
    rng = random.Random(seed)
    groups: dict[str, list[int]] = {}
    for index, raw_case in enumerate(raw_cases):
        groups.setdefault(_ground_truth_key(raw_case), []).append(index)

    train_indices: list[int] = []
    test_indices: list[int] = []
    for indices in groups.values():
        shuffled = list(indices)
        rng.shuffle(shuffled)
        if len(shuffled) == 1:
            train_indices.extend(shuffled)
            continue
        train_count = round(len(shuffled) * train_ratio)
        train_count = min(max(train_count, 1), len(shuffled) - 1)
        train_indices.extend(shuffled[:train_count])
        test_indices.extend(shuffled[train_count:])

    if not test_indices and len(train_indices) > 1:
        test_indices.append(train_indices.pop())
    train_indices.sort()
    test_indices.sort()
    return train_indices, test_indices


def _ground_truth_key(raw_case: dict[str, Any]) -> str:
    expected = raw_case.get("expected", raw_case.get("expected_output"))
    if isinstance(expected, dict):
        if "ground_truth" in expected:
            return _stable_json(expected["ground_truth"])
        if "primary" in expected:
            return _stable_json(expected["primary"])
    return _stable_json(expected)


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _expected_options(expected: Any) -> list[Any]:
    if expected is None:
        return []
    if isinstance(expected, dict):
        acceptable = expected.get("acceptable_outputs")
        if isinstance(acceptable, list) and acceptable:
            return acceptable
        if "primary" in expected:
            return [expected["primary"]]
        if "ground_truth" in expected:
            parsed = _parse_ground_truth_alternatives(expected["ground_truth"])
            return parsed if parsed else [expected["ground_truth"]]
    return [expected]


def _parse_ground_truth_alternatives(value: Any) -> list[Any]:
    if not isinstance(value, str):
        return [value]
    parts = [part.strip() for part in value.split(" or ") if part.strip()]
    parsed: list[Any] = []
    for part in parts:
        try:
            parsed.append(json.loads(part))
        except json.JSONDecodeError:
            return []
    return parsed


def _parse_json_output(output_text: str) -> tuple[Any, str | None]:
    text = output_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return output_text, str(exc)


def _matches_expected(output: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(output, dict) and _dict_contains(output, expected)
    return output == expected


def _dict_contains(output: dict[str, Any], expected: dict[str, Any]) -> bool:
    for key, expected_value in expected.items():
        if key not in output:
            return False
        output_value = output[key]
        if isinstance(expected_value, dict):
            if not isinstance(output_value, dict) or not _dict_contains(output_value, expected_value):
                return False
        elif output_value != expected_value:
            return False
    return True
