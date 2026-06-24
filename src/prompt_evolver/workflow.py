from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .model import DspyTargetModel, ModelConfig
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
    client = DspyTargetModel(model_config)
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
