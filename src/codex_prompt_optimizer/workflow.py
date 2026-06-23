from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .model import DspyTargetModel, ModelConfig
from .renderer import extract_mustache_variables, missing_variables, render_template
from .schemas import compute_metrics, judgement_cases, normalize_variables_file
from .storage import (
    append_jsonl,
    load_json,
    read_jsonl,
    sha256_text,
    utc_now_iso,
    write_json,
    write_jsonl,
)

SCHEMA_VERSION = "1.0"
GUIDANCE_START = "<!-- codex-prompt-opt:optimization-guidance:start -->"
GUIDANCE_END = "<!-- codex-prompt-opt:optimization-guidance:end -->"


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
            "required_output_file": "judgement.json",
            "required_scores": ["binary_score", "score_100"],
            "do_not_modify_variables_file": True,
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


def ensure_candidate_prompt(
    prompt_template: Path,
    workdir: Path,
    candidate_id: str,
    parent_candidate_id: str | None = None,
) -> Path:
    prompt_dir = workdir / "prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    candidate_prompt = prompt_dir / f"{candidate_id}.md"
    shutil.copyfile(prompt_template, candidate_prompt)
    template = candidate_prompt.read_text(encoding="utf-8")
    append_candidate_event(
        workdir,
        {
            "event": "candidate_started",
            "candidate_id": candidate_id,
            "parent_candidate_id": parent_candidate_id,
            "prompt_path": str(candidate_prompt),
            "prompt_template_sha256": sha256_text(template),
        },
    )
    return candidate_prompt


def optimize_step(
    prompt_template: Path,
    variables_file: Path,
    workdir: Path,
    candidate_id: str,
    model_config: ModelConfig,
    target_pass_rate: float = 1.0,
    target_average_score_100: float = 90.0,
) -> dict[str, Any]:
    candidate_prompt = ensure_candidate_prompt(prompt_template, workdir, candidate_id)
    rendered_path = workdir / f"rendered_cases_{candidate_id}.jsonl"
    outputs_path = workdir / f"target_outputs_{candidate_id}.jsonl"
    judge_pack_path = workdir / f"judge_pack_{candidate_id}.json"
    render_cases(candidate_prompt, variables_file, rendered_path, candidate_id)
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
        "candidate_prompt": str(candidate_prompt),
        "rendered_cases": str(rendered_path),
        "target_outputs": str(outputs_path),
        "judge_pack": str(judge_pack_path),
        "next_action": "Codex should fill the judgement_output_template and save judgement.json.",
    }


def ingest_judgement(
    judgement_file: Path,
    workdir: Path,
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
    stored_path = workdir / f"judgement_{candidate_id}.json"
    inferred_prompt_path = workdir / "prompts" / f"{candidate_id}.md"
    prompt_path = str(inferred_prompt_path) if inferred_prompt_path.exists() else None
    judgement = {
        **judgement,
        "computed_metrics": metrics,
        "computed_success": {
            "target_pass_rate": target_pass_rate,
            "target_average_score_100": target_average_score_100,
            "meets_success_criteria": meets,
        },
    }
    write_json(stored_path, judgement)
    append_candidate_event(
        workdir,
        {
            "event": "judgement_ingested",
            "candidate_id": candidate_id,
            **({"prompt_path": prompt_path} if prompt_path else {}),
            "judgement_path": str(stored_path),
            "metrics": metrics,
            "meets_success_criteria": meets,
        },
    )
    return {
        "candidate_id": candidate_id,
        "judgement_path": str(stored_path),
        "metrics": metrics,
        "meets_success_criteria": meets,
    }


def propose_prompt(
    prompt_template: Path,
    judgement_file: Path,
    out: Path,
    workdir: Path | None = None,
    candidate_id: str | None = None,
    parent_candidate_id: str | None = None,
    max_failures: int = 8,
) -> dict[str, Any]:
    template = prompt_template.read_text(encoding="utf-8")
    judgement = load_json(judgement_file)
    if not isinstance(judgement, dict):
        raise ValidationError("judgement JSON must be an object")
    cases = judgement_cases(judgement)
    failing = [
        case
        for case in cases
        if case["binary_score"] == 0 or int(case["score_100"]) < 90
    ]
    guidance = build_gepa_lite_guidance(failing[:max_failures])
    next_template = replace_guidance_block(template, guidance)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(next_template, encoding="utf-8")

    result = {
        "parent_prompt": str(prompt_template),
        "next_prompt": str(out),
        "candidate_id": candidate_id,
        "parent_candidate_id": parent_candidate_id or judgement.get("candidate_id"),
        "failing_case_count": len(failing),
        "guidance_added": bool(guidance),
    }
    if workdir:
        append_candidate_event(
            workdir,
            {
                "event": "candidate_proposed",
                "candidate_id": candidate_id or out.stem,
                "parent_candidate_id": parent_candidate_id or judgement.get("candidate_id"),
                "prompt_path": str(out),
                "source_judgement_path": str(judgement_file),
                "prompt_template_sha256": sha256_text(next_template),
                "failing_case_count": len(failing),
            },
        )
    return result


def build_gepa_lite_guidance(failing_cases: list[dict[str, Any]]) -> str:
    if not failing_cases:
        return ""
    tag_counts = Counter(
        tag for case in failing_cases for tag in case.get("failure_tags", [])
    )
    lines = [
        GUIDANCE_START,
        "",
        "## Optimization Guidance",
        "",
        "Apply these requirements before producing the final answer:",
    ]
    if tag_counts:
        top_tags = ", ".join(f"{tag} ({count})" for tag, count in tag_counts.most_common(8))
        lines.append(f"- Avoid recurring failure modes: {top_tags}.")
    for case in failing_cases:
        advice = (case.get("improvement_advice") or "").strip()
        rationale = (case.get("rationale") or "").strip()
        case_id = case["case_id"]
        if advice:
            lines.append(f"- Case {case_id}: {advice}")
        elif rationale:
            lines.append(f"- Case {case_id}: address this failure: {rationale}")
    lines.extend(
        [
            "- Preserve the original task intent and all Mustache variables.",
            "- Follow the requested output format exactly when one is specified.",
            "",
            GUIDANCE_END,
        ]
    )
    return "\n".join(lines)


def replace_guidance_block(template: str, guidance: str) -> str:
    start = template.find(GUIDANCE_START)
    end = template.find(GUIDANCE_END)
    if start != -1 and end != -1 and end > start:
        end += len(GUIDANCE_END)
        stripped = template[:start].rstrip()
        suffix = template[end:].lstrip()
        if guidance:
            return "\n\n".join(part for part in (stripped, guidance, suffix) if part) + "\n"
        return "\n\n".join(part for part in (stripped, suffix) if part) + "\n"
    if not guidance:
        return template
    return template.rstrip() + "\n\n" + guidance + "\n"


def finalize_best(
    workdir: Path,
    out_dir: Path,
    target_pass_rate: float = 1.0,
    target_average_score_100: float = 90.0,
) -> dict[str, Any]:
    index = candidate_index(workdir)
    judged = [candidate for candidate in index.values() if "metrics" in candidate]
    if not judged:
        raise ValidationError("no judged candidates found; run ingest-judgement first")
    judged.sort(
        key=lambda item: (
            item["metrics"]["pass_rate"],
            item["metrics"]["average_score_100"],
            item.get("updated_at", ""),
        ),
        reverse=True,
    )
    best = judged[0]
    prompt_path = best.get("prompt_path")
    if not prompt_path:
        raise ValidationError(f"best candidate {best['candidate_id']} has no prompt_path")
    source_prompt = Path(prompt_path)
    if not source_prompt.exists():
        raise ValidationError(f"best prompt does not exist: {source_prompt}")

    out_dir.mkdir(parents=True, exist_ok=True)
    best_prompt_path = out_dir / "best_prompt.md"
    shutil.copyfile(source_prompt, best_prompt_path)
    metrics = best["metrics"]
    meets = (
        metrics["pass_rate"] >= target_pass_rate
        and metrics["average_score_100"] >= target_average_score_100
    )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "best_candidate_id": best["candidate_id"],
        "best_prompt": str(best_prompt_path),
        "metrics": metrics,
        "target_pass_rate": target_pass_rate,
        "target_average_score_100": target_average_score_100,
        "meets_success_criteria": meets,
        "judged_candidate_count": len(judged),
    }
    write_json(out_dir / "summary.json", summary)
    write_run_report(out_dir / "run_report.md", summary, judged)
    return summary


def write_run_report(path: Path, summary: dict[str, Any], judged: list[dict[str, Any]]) -> None:
    lines = [
        "# Prompt Optimization Report",
        "",
        f"- Best candidate: `{summary['best_candidate_id']}`",
        f"- Pass rate: {summary['metrics']['pass_rate']:.2%}",
        f"- Average score_100: {summary['metrics']['average_score_100']:.2f}",
        f"- Meets success criteria: {summary['meets_success_criteria']}",
        f"- Judged candidates: {summary['judged_candidate_count']}",
        "",
        "## Candidate Scores",
        "",
    ]
    for candidate in judged:
        metrics = candidate["metrics"]
        lines.append(
            f"- `{candidate['candidate_id']}`: pass_rate={metrics['pass_rate']:.2%}, "
            f"average_score_100={metrics['average_score_100']:.2f}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def append_candidate_event(workdir: Path, record: dict[str, Any]) -> None:
    append_jsonl(
        workdir / "candidates.jsonl",
        {
            "schema_version": SCHEMA_VERSION,
            "created_at": utc_now_iso(),
            **record,
        },
    )


def candidate_index(workdir: Path) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for event in read_jsonl(workdir / "candidates.jsonl"):
        candidate_id = str(event.get("candidate_id") or "unknown")
        current = index.setdefault(candidate_id, {})
        current.update({key: value for key, value in event.items() if key != "event"})
        current["candidate_id"] = candidate_id
        current["last_event"] = event.get("event")
        current["updated_at"] = event.get("created_at")
    return index
