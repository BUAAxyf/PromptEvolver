from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .model import ModelConfig
from .schemas import judgement_cases
from .storage import append_jsonl, load_json, read_jsonl, sha256_text, utc_now_iso, write_json
from .workflow import (
    blackbox_evaluate,
    finalize_prompt,
    ingest_judgement,
    optimize_step,
    split_train_test,
    validate_inputs,
)

STRICT_STATE_FILE = "strict_state.json"
STRICT_STATES = (
    "initialized",
    "train_pack_ready",
    "judgement_ingested",
    "hidden_scored",
    "logged",
)
REQUIRED_ARTIFACT_KEYS = (
    "prompt",
    "rendered_cases",
    "target_outputs",
    "judge_pack",
    "subagent_reviews",
    "judgement",
    "blackbox_score",
)
HIDDEN_FORBIDDEN_KEYS = {
    "case_scores",
    "case_ids",
    "variables_file",
    "rendered_prompts",
    "rendered_prompt",
    "target_outputs",
    "target_output",
    "judge_prompts",
    "judge_prompt",
}


def strict_init(
    variables_file: Path,
    prompt_template: Path,
    out_dir: Path,
    max_iterations: int = 100,
    target_pass_rate: float = 0.95,
    train_ratio: float = 0.7,
    seed: int = 13,
    target_average_score_100: float = 95.0,
) -> dict[str, Any]:
    if max_iterations <= 0:
        raise ValidationError("max_iterations must be positive")
    if not 0 < target_pass_rate <= 1:
        raise ValidationError("target_pass_rate must be between 0 and 1")

    state_path = _state_path(out_dir)
    if state_path.exists():
        raise ValidationError(f"strict state already exists: {state_path}")

    validation = validate_inputs(prompt_template, variables_file)
    if not validation["valid"]:
        raise ValidationError("input validation failed before strict workflow initialization")

    out_dir.mkdir(parents=True, exist_ok=True)
    train_json = out_dir / "train.json"
    test_json = out_dir / "test.json"
    split_report = split_train_test(variables_file, train_json, test_json, train_ratio, seed)
    train_validation = validate_inputs(prompt_template, train_json)
    if not train_validation["valid"]:
        raise ValidationError("strict train split has missing template variables")
    test_validation = validate_inputs(prompt_template, test_json)
    if not test_validation["valid"]:
        raise ValidationError(
            "strict hidden split has missing template variables; details are redacted"
        )

    now = utc_now_iso()
    state = {
        "schema_version": "1.0",
        "strict_workflow": True,
        "created_at": now,
        "updated_at": now,
        "config": {
            "source_variables_file": str(variables_file),
            "source_prompt": str(prompt_template),
            "train_json": str(train_json),
            "test_json": str(test_json),
            "max_iterations": max_iterations,
            "target_pass_rate": target_pass_rate,
            "target_average_score_100": target_average_score_100,
            "train_ratio": train_ratio,
            "seed": seed,
            "stratify_by": split_report["stratify_by"],
            "case_count": split_report["case_count"],
            "train_count": split_report["train_count"],
            "test_count": split_report["test_count"],
        },
        "candidates": {},
        "termination": {
            "target_reached": False,
            "reason": None,
            "selected_candidate_id": None,
            "logged_candidate_count": 0,
        },
    }
    _save_state(out_dir, state)
    return state


def strict_train_candidate(
    source_prompt: Path,
    out_dir: Path,
    candidate_id: str,
    model_config: ModelConfig,
    parent_candidate_id: str | None = None,
    strategy: str = "",
) -> dict[str, Any]:
    _validate_candidate_id(candidate_id)
    state = _load_state(out_dir)
    _ensure_can_add_candidate(state)
    if candidate_id in state["candidates"]:
        raise ValidationError(f"candidate already exists in strict state: {candidate_id}")

    prompts_dir = out_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = prompts_dir / f"{candidate_id}.md"
    _copy_file_if_needed(source_prompt, prompt_path)

    state["candidates"][candidate_id] = {
        "candidate_id": candidate_id,
        "status": "initialized",
        "parent_candidate_id": parent_candidate_id,
        "strategy": strategy,
        "prompt_path": str(prompt_path),
        "prompt_sha256": sha256_text(prompt_path.read_text(encoding="utf-8")),
        "artifact_paths": {
            "prompt": str(prompt_path),
            "rendered_cases": None,
            "target_outputs": None,
            "judge_pack": None,
            "subagent_reviews": None,
            "judgement": None,
            "blackbox_score": None,
        },
        "metrics": None,
        "hidden_eval": None,
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
    }
    _save_state(out_dir, state)

    result = optimize_step(
        prompt_path,
        Path(state["config"]["train_json"]),
        out_dir,
        candidate_id,
        model_config,
        target_pass_rate=state["config"]["target_pass_rate"],
        target_average_score_100=state["config"]["target_average_score_100"],
    )

    state = _load_state(out_dir)
    candidate = state["candidates"][candidate_id]
    candidate["status"] = "train_pack_ready"
    candidate["artifact_paths"]["rendered_cases"] = result["rendered_cases"]
    candidate["artifact_paths"]["target_outputs"] = result["target_outputs"]
    candidate["artifact_paths"]["judge_pack"] = result["judge_pack"]
    candidate["updated_at"] = utc_now_iso()
    _save_state(out_dir, state)
    return candidate


def strict_ingest_candidate(
    judgement_file: Path,
    out_dir: Path,
    candidate_id: str,
    subagent_reviews: Path,
) -> dict[str, Any]:
    state, candidate = _load_candidate_in_status(out_dir, candidate_id, "train_pack_ready")
    judge_pack_path = Path(candidate["artifact_paths"]["judge_pack"])
    _validate_judgement_matches_pack(judgement_file, judge_pack_path, candidate_id)

    subagent_reviews_path = out_dir / f"subagent_reviews_{candidate_id}.json"
    _copy_file_if_needed(subagent_reviews, subagent_reviews_path)
    ingest_result = ingest_judgement(
        judgement_file,
        out_dir=out_dir,
        target_pass_rate=state["config"]["target_pass_rate"],
        target_average_score_100=state["config"]["target_average_score_100"],
    )
    judgement_path = ingest_result["judgement_path"]
    if not judgement_path:
        raise ValidationError("ingest_judgement did not return a judgement path")

    candidate["status"] = "judgement_ingested"
    candidate["artifact_paths"]["subagent_reviews"] = str(subagent_reviews_path)
    candidate["artifact_paths"]["judgement"] = judgement_path
    candidate["metrics"] = ingest_result["metrics"]
    candidate["meets_training_success_criteria"] = ingest_result["meets_success_criteria"]
    candidate["updated_at"] = utc_now_iso()
    _save_state(out_dir, state)
    return candidate


def strict_blackbox_candidate(
    out_dir: Path,
    candidate_id: str,
    target_model_config: ModelConfig,
    evaluator_model_config: ModelConfig,
) -> dict[str, Any]:
    state, candidate = _load_candidate_in_status(out_dir, candidate_id, "judgement_ingested")
    result = blackbox_evaluate(
        Path(candidate["prompt_path"]),
        Path(state["config"]["test_json"]),
        out_dir,
        candidate_id,
        target_model_config,
        evaluator_model_config,
    )
    hidden_eval = _hidden_eval_summary(result)
    candidate["status"] = "hidden_scored"
    candidate["artifact_paths"]["blackbox_score"] = result["score_report"]
    candidate["hidden_eval"] = hidden_eval
    candidate["updated_at"] = utc_now_iso()
    _save_state(out_dir, state)
    return candidate


def strict_log_candidate(out_dir: Path, candidate_id: str) -> dict[str, Any]:
    state, candidate = _load_candidate_in_status(out_dir, candidate_id, "hidden_scored")
    _validate_candidate_complete(candidate_id, candidate)
    existing_logs = _log_records_by_candidate(out_dir)
    if candidate_id in existing_logs:
        raise ValidationError(f"candidate is already present in optimization log: {candidate_id}")

    record = {
        "schema_version": "1.0",
        "candidate_id": candidate_id,
        "parent_candidate_id": candidate["parent_candidate_id"],
        "prompt_path": candidate["prompt_path"],
        "prompt_sha256": candidate["prompt_sha256"],
        "strategy": candidate["strategy"],
        "subagent_review_path": candidate["artifact_paths"]["subagent_reviews"],
        "judgement_path": candidate["artifact_paths"]["judgement"],
        "metrics": candidate["metrics"],
        "hidden_eval": candidate["hidden_eval"],
        "optimization_suggestions": [],
        "artifact_paths": candidate["artifact_paths"],
    }
    append_jsonl(out_dir / "optimization_log.jsonl", record)

    candidate["status"] = "logged"
    candidate["logged_at"] = utc_now_iso()
    candidate["updated_at"] = utc_now_iso()
    _refresh_termination(state)
    _save_state(out_dir, state)
    return {"candidate": candidate, "termination": state["termination"]}


def strict_verify(out_dir: Path) -> dict[str, Any]:
    state = _load_state(out_dir)
    errors: list[str] = []
    logs = _log_records_by_candidate(out_dir)

    for candidate_id, candidate in sorted(state["candidates"].items()):
        if candidate.get("status") != "logged":
            errors.append(f"{candidate_id}: status is {candidate.get('status')}, expected logged")
        _collect_candidate_errors(candidate_id, candidate, logs, errors)

    for candidate_id in _artifact_candidate_ids(out_dir):
        if candidate_id not in state["candidates"]:
            errors.append(f"{candidate_id}: artifact exists but candidate is missing from strict state")

    return {
        "schema_version": "1.0",
        "out_dir": str(out_dir),
        "valid": not errors,
        "candidate_count": len(state["candidates"]),
        "logged_candidate_count": sum(
            1 for candidate in state["candidates"].values() if candidate.get("status") == "logged"
        ),
        "errors": errors,
    }


def strict_finalize(out_dir: Path, candidate_id: str | None = None) -> dict[str, Any]:
    verify_report = strict_verify(out_dir)
    if not verify_report["valid"]:
        raise ValidationError(
            "strict verify failed before finalize: "
            + "; ".join(verify_report["errors"])
        )
    state = _load_state(out_dir)
    logged = {
        current_id: candidate
        for current_id, candidate in state["candidates"].items()
        if candidate.get("status") == "logged"
    }
    if not logged:
        raise ValidationError("strict finalize requires at least one logged candidate")
    if candidate_id is not None:
        _validate_candidate_id(candidate_id)
        if candidate_id not in logged:
            raise ValidationError(f"candidate is not logged in strict state: {candidate_id}")
        selected_id = candidate_id
        selected_by = "explicit_candidate_id"
    else:
        selected_id = max(
            sorted(logged),
            key=lambda current_id: logged[current_id]["hidden_eval"]["pass_rate"],
        )
        selected_by = "highest_hidden_blackbox_pass_rate"

    selected = logged[selected_id]
    state["termination"]["selected_candidate_id"] = selected_id
    state["termination"]["finalized_at"] = utc_now_iso()
    summary = finalize_prompt(
        Path(selected["prompt_path"]),
        Path(selected["artifact_paths"]["judgement"]),
        out_dir / "final",
        target_pass_rate=state["config"]["target_pass_rate"],
        target_average_score_100=state["config"]["target_average_score_100"],
    )
    summary = {
        **summary,
        "selected_candidate_id": selected_id,
        "selected_by": selected_by,
        "hidden_eval": selected["hidden_eval"],
        "iteration_count": verify_report["logged_candidate_count"],
        "strict_verify": verify_report,
        "termination": state["termination"],
    }
    write_json(out_dir / "final" / "summary.json", summary)

    _save_state(out_dir, state)
    return summary


def _state_path(out_dir: Path) -> Path:
    return out_dir / STRICT_STATE_FILE


def _load_state(out_dir: Path) -> dict[str, Any]:
    path = _state_path(out_dir)
    if not path.exists():
        raise ValidationError(f"strict state does not exist: {path}")
    state = load_json(path)
    if not isinstance(state, dict) or state.get("strict_workflow") is not True:
        raise ValidationError(f"invalid strict state file: {path}")
    if not isinstance(state.get("candidates"), dict):
        raise ValidationError("strict state must contain a candidates object")
    return state


def _save_state(out_dir: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = utc_now_iso()
    write_json(_state_path(out_dir), state)


def _load_candidate_in_status(
    out_dir: Path,
    candidate_id: str,
    expected_status: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _validate_candidate_id(candidate_id)
    state = _load_state(out_dir)
    candidate = state["candidates"].get(candidate_id)
    if not isinstance(candidate, dict):
        raise ValidationError(f"candidate is missing from strict state: {candidate_id}")
    status = candidate.get("status")
    if status != expected_status:
        raise ValidationError(
            f"candidate {candidate_id} must be {expected_status}; current status is {status}"
        )
    return state, candidate


def _validate_candidate_id(candidate_id: str) -> None:
    if not candidate_id or "/" in candidate_id or "\\" in candidate_id or candidate_id in {".", ".."}:
        raise ValidationError(f"invalid candidate_id: {candidate_id!r}")


def _ensure_can_add_candidate(state: dict[str, Any]) -> None:
    logged_count = sum(
        1 for candidate in state["candidates"].values() if candidate.get("status") == "logged"
    )
    if logged_count >= state["config"]["max_iterations"]:
        raise ValidationError("strict workflow has reached max_iterations")
    if state.get("termination", {}).get("target_reached"):
        raise ValidationError("strict workflow target has already been reached")


def _copy_file_if_needed(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        if source.resolve() == target.resolve():
            return
    except FileNotFoundError:
        pass
    shutil.copyfile(source, target)


def _validate_judgement_matches_pack(
    judgement_file: Path,
    judge_pack_path: Path,
    candidate_id: str,
) -> None:
    pack = load_json(judge_pack_path)
    judgement = load_json(judgement_file)
    if not isinstance(pack, dict):
        raise ValidationError("judge pack must be a JSON object")
    if not isinstance(judgement, dict):
        raise ValidationError("judgement must be a JSON object")
    if str(judgement.get("candidate_id")) != candidate_id:
        raise ValidationError(
            f"judgement candidate_id must be {candidate_id}, got {judgement.get('candidate_id')}"
        )
    pack_cases = pack.get("cases")
    if not isinstance(pack_cases, list):
        raise ValidationError("judge pack must contain cases")
    pack_case_ids = [case.get("case_id") for case in pack_cases if isinstance(case, dict)]
    judgement_case_ids = [case["case_id"] for case in judgement_cases(judgement)]
    if len(pack_case_ids) != len(judgement_case_ids) or set(pack_case_ids) != set(judgement_case_ids):
        raise ValidationError("judgement case IDs must match judge pack case IDs exactly")


def _hidden_eval_summary(result: dict[str, Any]) -> dict[str, Any]:
    summary_keys = (
        "score_report",
        "pass_rate",
        "passed_count",
        "case_count",
        "scored_count",
        "evaluator_parse_error_count",
        "invalid_score_count",
        "content_redaction",
    )
    return {key: result[key] for key in summary_keys if key in result}


def _validate_candidate_complete(candidate_id: str, candidate: dict[str, Any]) -> None:
    errors: list[str] = []
    _collect_artifact_errors(candidate_id, candidate, errors)
    if not candidate.get("metrics"):
        errors.append(f"{candidate_id}: missing training metrics")
    if not candidate.get("hidden_eval"):
        errors.append(f"{candidate_id}: missing hidden_eval metrics")
    _collect_hidden_eval_errors(candidate_id, candidate, errors)
    if errors:
        raise ValidationError("; ".join(errors))


def _collect_candidate_errors(
    candidate_id: str,
    candidate: dict[str, Any],
    logs: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    _collect_artifact_errors(candidate_id, candidate, errors)
    if not candidate.get("metrics"):
        errors.append(f"{candidate_id}: missing training metrics")
    if not candidate.get("hidden_eval"):
        errors.append(f"{candidate_id}: missing hidden_eval metrics")
    _collect_hidden_eval_errors(candidate_id, candidate, errors)
    log = logs.get(candidate_id)
    if log is None:
        errors.append(f"{candidate_id}: missing optimization_log entry")
    elif not log.get("metrics") or not log.get("hidden_eval"):
        errors.append(f"{candidate_id}: optimization_log entry has null metrics or hidden_eval")


def _collect_artifact_errors(
    candidate_id: str,
    candidate: dict[str, Any],
    errors: list[str],
) -> None:
    artifacts = candidate.get("artifact_paths")
    if not isinstance(artifacts, dict):
        errors.append(f"{candidate_id}: missing artifact_paths")
        return
    for key in REQUIRED_ARTIFACT_KEYS:
        value = artifacts.get(key)
        if not value:
            errors.append(f"{candidate_id}: missing artifact path {key}")
            continue
        if not Path(value).exists():
            errors.append(f"{candidate_id}: artifact path does not exist for {key}: {value}")


def _collect_hidden_eval_errors(
    candidate_id: str,
    candidate: dict[str, Any],
    errors: list[str],
) -> None:
    hidden_eval = candidate.get("hidden_eval")
    if not isinstance(hidden_eval, dict):
        return
    forbidden = sorted(key for key in HIDDEN_FORBIDDEN_KEYS if key in hidden_eval)
    if forbidden:
        errors.append(f"{candidate_id}: hidden_eval contains forbidden keys: {', '.join(forbidden)}")


def _log_records_by_candidate(out_dir: Path) -> dict[str, dict[str, Any]]:
    log_path = out_dir / "optimization_log.jsonl"
    records: dict[str, dict[str, Any]] = {}
    for record in read_jsonl(log_path):
        candidate_id = record.get("candidate_id")
        if isinstance(candidate_id, str):
            records[candidate_id] = record
    return records


def _artifact_candidate_ids(out_dir: Path) -> set[str]:
    candidates: set[str] = set()
    patterns = (
        ("blackbox_score_", ".json"),
        ("judgement_", ".json"),
        ("judge_pack_", ".json"),
        ("rendered_cases_", ".jsonl"),
        ("target_outputs_", ".jsonl"),
        ("subagent_reviews_", ".json"),
    )
    for prefix, suffix in patterns:
        for path in out_dir.glob(f"{prefix}*{suffix}"):
            candidates.add(path.name[len(prefix) : -len(suffix)])
    prompt_dir = out_dir / "prompts"
    prompt_paths = prompt_dir.glob("*.md") if prompt_dir.exists() else ()
    for path in prompt_paths:
        candidates.add(path.stem)
    return candidates


def _refresh_termination(state: dict[str, Any]) -> None:
    logged = {
        candidate_id: candidate
        for candidate_id, candidate in state["candidates"].items()
        if candidate.get("status") == "logged"
    }
    logged_count = len(logged)
    target_pass_rate = state["config"]["target_pass_rate"]
    target_reached_candidates = [
        candidate_id
        for candidate_id, candidate in logged.items()
        if candidate.get("hidden_eval", {}).get("pass_rate", 0.0) > target_pass_rate
    ]
    termination = state.setdefault("termination", {})
    termination["logged_candidate_count"] = logged_count
    if target_reached_candidates:
        selected = max(
            sorted(target_reached_candidates),
            key=lambda current_id: logged[current_id]["hidden_eval"]["pass_rate"],
        )
        termination["target_reached"] = True
        termination["reason"] = "target_pass_rate_reached"
        termination["selected_candidate_id"] = selected
    elif logged_count >= state["config"]["max_iterations"]:
        termination["target_reached"] = False
        termination["reason"] = "max_iterations_reached"
    else:
        termination["target_reached"] = False
        termination["reason"] = None
