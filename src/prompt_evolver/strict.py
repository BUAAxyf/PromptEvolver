from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .model import ModelConfig
from .schemas import judgement_cases, normalize_variables_file
from .storage import append_jsonl, load_json, read_jsonl, sha256_text, utc_now_iso, write_json
from .workflow import (
    aggregate_exact_evaluate,
    audit_variables_file,
    blackbox_evaluate,
    finalize_prompt,
    ingest_judgement,
    optimize_step,
    split_train_test,
    validate_inputs,
    write_run_report,
)

STRICT_STATE_FILE = "strict_state.json"
STRICT_STATES = (
    "initialized",
    "train_pack_ready",
    "judgement_ingested",
    "dev_scored",
    "logged",
)
REQUIRED_TRAIN_ARTIFACT_KEYS = (
    "prompt",
    "rendered_cases",
    "target_outputs",
    "judge_pack",
    "subagent_reviews",
    "judgement",
)
EVAL_FORBIDDEN_KEYS = {
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
    train_json: Path | None = None,
    dev_json: Path | None = None,
    test_json: Path | None = None,
) -> dict[str, Any]:
    if max_iterations <= 0:
        raise ValidationError("max_iterations must be positive")
    if not 0 < target_pass_rate <= 1:
        raise ValidationError("target_pass_rate must be between 0 and 1")

    state_path = _state_path(out_dir)
    if state_path.exists():
        raise ValidationError(f"strict state already exists: {state_path}")

    out_dir.mkdir(parents=True, exist_ok=True)
    explicit_files = (train_json, dev_json, test_json)
    if any(path is not None for path in explicit_files) and not all(path is not None for path in explicit_files):
        raise ValidationError("train_json, dev_json, and test_json must be provided together")

    if all(path is not None for path in explicit_files):
        resolved_train_json = Path(train_json)  # type: ignore[arg-type]
        resolved_dev_json = Path(dev_json)  # type: ignore[arg-type]
        resolved_test_json = Path(test_json)  # type: ignore[arg-type]
        for dataset_name, dataset_path in (
            ("train", resolved_train_json),
            ("dev", resolved_dev_json),
            ("test", resolved_test_json),
        ):
            audit = audit_variables_file(dataset_path)
            if not audit["valid_for_three_way_evaluation"]:
                raise ValidationError(
                    f"{dataset_name} dataset is not ready for three-way evaluation: "
                    f"conflicting_duplicates={len(audit['conflicting_duplicate_groups'])}, "
                    f"missing_split_group={audit['missing_split_group_count']}, "
                    f"unadjudicated={audit['unadjudicated_count']}"
                )
        _validate_split_group_isolation(
            {
                "train": resolved_train_json,
                "dev": resolved_dev_json,
                "test": resolved_test_json,
            }
        )
        split_mode = "explicit_three_way"
        split_report = {
            "stratify_by": "provided_files",
            "case_count": sum(
                validate_inputs(prompt_template, path)["case_count"]
                for path in (resolved_train_json, resolved_dev_json, resolved_test_json)
            ),
            "train_count": validate_inputs(prompt_template, resolved_train_json)["case_count"],
            "dev_count": validate_inputs(prompt_template, resolved_dev_json)["case_count"],
            "test_count": validate_inputs(prompt_template, resolved_test_json)["case_count"],
        }
    else:
        validation = validate_inputs(prompt_template, variables_file)
        if not validation["valid"]:
            raise ValidationError("input validation failed before strict workflow initialization")
        resolved_train_json = out_dir / "train.json"
        resolved_dev_json = out_dir / "dev.json"
        split_report = split_train_test(
            variables_file,
            resolved_train_json,
            resolved_dev_json,
            train_ratio,
            seed,
        )
        resolved_test_json = None
        split_mode = "legacy_two_way"
        split_report["dev_count"] = split_report.pop("test_count")
        split_report["test_count"] = 0

    train_validation = validate_inputs(prompt_template, resolved_train_json)
    if not train_validation["valid"]:
        raise ValidationError("strict train split has missing template variables")
    dev_validation = validate_inputs(prompt_template, resolved_dev_json)
    if not dev_validation["valid"]:
        raise ValidationError(
            "strict dev split has missing template variables; details are redacted"
        )
    if resolved_test_json is not None:
        test_validation = validate_inputs(prompt_template, resolved_test_json)
        if not test_validation["valid"]:
            raise ValidationError(
                "strict final test split has missing template variables; details are redacted"
            )

    now = utc_now_iso()
    state = {
        "schema_version": "2.0",
        "strict_workflow": True,
        "created_at": now,
        "updated_at": now,
        "config": {
            "source_variables_file": str(variables_file),
            "source_prompt": str(prompt_template),
            "split_mode": split_mode,
            "train_json": str(resolved_train_json),
            "dev_json": str(resolved_dev_json),
            "test_json": str(resolved_test_json) if resolved_test_json is not None else None,
            "max_iterations": max_iterations,
            "target_pass_rate": target_pass_rate,
            "target_average_score_100": target_average_score_100,
            "train_ratio": train_ratio,
            "seed": seed,
            "stratify_by": split_report["stratify_by"],
            "case_count": split_report["case_count"],
            "train_count": split_report["train_count"],
            "dev_count": split_report["dev_count"],
            "test_count": split_report["test_count"],
        },
        "candidates": {},
        "termination": {
            "target_reached": False,
            "reason": None,
            "selected_candidate_id": None,
            "logged_candidate_count": 0,
        },
        "final_test_eval": None,
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
            "dev_score": None,
        },
        "metrics": None,
        "dev_eval": None,
        "optimization_suggestions": [],
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
    }
    _save_state(out_dir, state)

    try:
        result = optimize_step(
            prompt_path,
            Path(state["config"]["train_json"]),
            out_dir,
            candidate_id,
            model_config,
            target_pass_rate=state["config"]["target_pass_rate"],
            target_average_score_100=state["config"]["target_average_score_100"],
        )
    except BaseException:
        state = _load_state(out_dir)
        state["candidates"].pop(candidate_id, None)
        _save_state(out_dir, state)
        _remove_candidate_artifacts(out_dir, candidate_id)
        raise

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
    optimization_suggestions = _validate_and_collect_reviews(
        subagent_reviews,
        judgement_file,
        candidate_id,
    )

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
    candidate["optimization_suggestions"] = optimization_suggestions
    candidate["meets_training_success_criteria"] = ingest_result["meets_success_criteria"]
    candidate["updated_at"] = utc_now_iso()
    _save_state(out_dir, state)
    return candidate


def strict_dev_score_candidate(
    out_dir: Path,
    candidate_id: str,
    target_model_config: ModelConfig,
    evaluator_model_config: ModelConfig | None = None,
    scorer: str = "exact",
) -> dict[str, Any]:
    state, candidate = _load_candidate_in_status(out_dir, candidate_id, "judgement_ingested")
    if scorer == "exact":
        result = aggregate_exact_evaluate(
            Path(candidate["prompt_path"]),
            Path(state["config"]["dev_json"]),
            out_dir,
            candidate_id,
            target_model_config,
        )
    elif scorer == "llm":
        if evaluator_model_config is None:
            raise ValidationError("llm scorer requires evaluator_model_config")
        result = blackbox_evaluate(
            Path(candidate["prompt_path"]),
            Path(state["config"]["dev_json"]),
            out_dir,
            candidate_id,
            target_model_config,
            evaluator_model_config,
        )
    else:
        raise ValidationError("scorer must be exact or llm")
    dev_eval = _aggregate_eval_summary(result)
    candidate["status"] = "dev_scored"
    candidate["artifact_paths"]["dev_score"] = result["score_report"]
    candidate["dev_eval"] = dev_eval
    candidate["updated_at"] = utc_now_iso()
    _save_state(out_dir, state)
    return candidate


def strict_blackbox_candidate(
    out_dir: Path,
    candidate_id: str,
    target_model_config: ModelConfig,
    evaluator_model_config: ModelConfig,
) -> dict[str, Any]:
    """Compatibility wrapper for the legacy LLM-scored development command."""
    return strict_dev_score_candidate(
        out_dir,
        candidate_id,
        target_model_config,
        evaluator_model_config,
        scorer="llm",
    )


def strict_log_candidate(out_dir: Path, candidate_id: str) -> dict[str, Any]:
    state, candidate = _load_candidate_in_status(out_dir, candidate_id, "dev_scored")
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
        "dev_eval": candidate["dev_eval"],
        "optimization_suggestions": candidate["optimization_suggestions"],
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


def strict_final_eval(
    out_dir: Path,
    candidate_id: str,
    target_model_config: ModelConfig,
) -> dict[str, Any]:
    verify_report = strict_verify(out_dir)
    if not verify_report["valid"]:
        raise ValidationError(
            "strict verify failed before final evaluation: " + "; ".join(verify_report["errors"])
        )
    state = _load_state(out_dir)
    if state.get("final_test_eval") is not None:
        raise ValidationError("strict final evaluation can only run once")
    test_json = state["config"].get("test_json")
    if not test_json:
        raise ValidationError("strict final evaluation requires an explicit three-way test_json")
    candidate = state["candidates"].get(candidate_id)
    if not isinstance(candidate, dict) or candidate.get("status") != "logged":
        raise ValidationError(f"candidate is not logged in strict state: {candidate_id}")
    logged = {
        current_id: current
        for current_id, current in state["candidates"].items()
        if current.get("status") == "logged"
    }
    selected_id = max(sorted(logged), key=lambda current_id: _selection_key(logged[current_id]))
    if candidate_id != selected_id:
        raise ValidationError(
            f"final evaluation candidate must be the highest development-score candidate: {selected_id}"
        )

    result = aggregate_exact_evaluate(
        Path(candidate["prompt_path"]),
        Path(test_json),
        out_dir,
        candidate_id,
        target_model_config,
        report_prefix="final_test_score",
    )
    summary = _aggregate_eval_summary(result)
    state["final_test_eval"] = {
        "candidate_id": candidate_id,
        **summary,
    }
    state["termination"]["selected_candidate_id"] = candidate_id
    state["termination"]["reason"] = "final_test_completed"
    state["termination"]["final_test_completed"] = True
    _save_state(out_dir, state)
    return state["final_test_eval"]


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
    if state["config"].get("split_mode") == "explicit_three_way" and state.get("final_test_eval") is None:
        raise ValidationError("explicit three-way workflow requires strict final-eval before finalize")
    final_test_eval = state.get("final_test_eval")
    locked_candidate_id = (
        final_test_eval.get("candidate_id") if isinstance(final_test_eval, dict) else None
    )
    if locked_candidate_id is not None and candidate_id not in (None, locked_candidate_id):
        raise ValidationError(
            f"candidate must match the completed final evaluation: {locked_candidate_id}"
        )
    if locked_candidate_id is not None:
        selected_id = locked_candidate_id
        selected_by = "final_test_locked_candidate"
    elif candidate_id is not None:
        _validate_candidate_id(candidate_id)
        if candidate_id not in logged:
            raise ValidationError(f"candidate is not logged in strict state: {candidate_id}")
        selected_id = candidate_id
        selected_by = "explicit_candidate_id"
    else:
        selected_id = max(
            sorted(logged),
            key=lambda current_id: _selection_key(logged[current_id]),
        )
        selected_by = "highest_dev_score"

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
        "train_metrics": selected["metrics"],
        "dev_metrics": selected["dev_eval"],
        "final_test_metrics": final_test_eval,
        "train_meets_success_criteria": selected.get("meets_training_success_criteria", False),
        "dev_meets_success_criteria": (
            selected["dev_eval"]["pass_rate"] >= state["config"]["target_pass_rate"]
        ),
        "final_test_meets_success_criteria": (
            final_test_eval["pass_rate"] >= state["config"]["target_pass_rate"]
            if isinstance(final_test_eval, dict)
            else None
        ),
        "iteration_count": verify_report["logged_candidate_count"],
        "strict_verify": verify_report,
        "termination": state["termination"],
    }
    summary["meets_success_criteria"] = (
        summary["final_test_meets_success_criteria"]
        if summary["final_test_meets_success_criteria"] is not None
        else summary["dev_meets_success_criteria"]
    )
    write_json(out_dir / "final" / "summary.json", summary)
    write_run_report(out_dir / "final" / "run_report.md", summary)

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
    config = state.get("config", {})
    if "dev_json" not in config and config.get("test_json"):
        config["split_mode"] = "legacy_two_way"
        config["dev_json"] = config["test_json"]
        config["dev_count"] = config.get("test_count", 0)
        config["test_json"] = None
        config["test_count"] = 0
    state.setdefault("final_test_eval", None)
    for candidate in state["candidates"].values():
        if not isinstance(candidate, dict):
            continue
        if candidate.get("dev_eval") is None and candidate.get("hidden_eval") is not None:
            candidate["dev_eval"] = candidate["hidden_eval"]
        artifacts = candidate.get("artifact_paths")
        if isinstance(artifacts, dict) and not artifacts.get("dev_score"):
            artifacts["dev_score"] = artifacts.get("blackbox_score")
        candidate.setdefault(
            "optimization_suggestions",
            [{"type": "legacy", "guidance": "Legacy candidate without structured suggestions."}],
        )
        if candidate.get("status") == "hidden_scored":
            candidate["status"] = "dev_scored"
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
    if state.get("final_test_eval") is not None:
        raise ValidationError("strict workflow is locked after final evaluation")
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


def _remove_candidate_artifacts(out_dir: Path, candidate_id: str) -> None:
    for prefix, suffix in (
        ("rendered_cases_", ".jsonl"),
        ("target_outputs_", ".jsonl"),
        ("judge_pack_", ".json"),
    ):
        (out_dir / f"{prefix}{candidate_id}{suffix}").unlink(missing_ok=True)
    (out_dir / "prompts" / f"{candidate_id}.md").unlink(missing_ok=True)


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


def _validate_and_collect_reviews(
    reviews_file: Path,
    judgement_file: Path,
    candidate_id: str,
) -> list[dict[str, Any]]:
    payload = load_json(reviews_file)
    if not isinstance(payload, dict):
        raise ValidationError("subagent reviews must be a JSON object")
    if str(payload.get("candidate_id")) != candidate_id:
        raise ValidationError(
            f"subagent reviews candidate_id must be {candidate_id}, got {payload.get('candidate_id')}"
        )
    reviews = payload.get("reviews")
    if not isinstance(reviews, list) or not reviews:
        raise ValidationError("subagent reviews must contain at least one review")

    judgement = load_json(judgement_file)
    failed_case_ids = {
        case["case_id"] for case in judgement_cases(judgement) if case["binary_score"] == 0
    }
    reviewed_case_ids: set[str] = set()
    suggestions: list[dict[str, Any]] = []
    for review in reviews:
        if not isinstance(review, dict):
            raise ValidationError("each subagent review must be an object")
        case_reviews = review.get("case_judgements", [])
        if not isinstance(case_reviews, list):
            raise ValidationError("review case_judgements must be an array")
        for case_review in case_reviews:
            if isinstance(case_review, dict) and isinstance(case_review.get("case_id"), str):
                reviewed_case_ids.add(case_review["case_id"])
        loopholes = review.get("prompt_loopholes", [])
        if not isinstance(loopholes, list):
            raise ValidationError("review prompt_loopholes must be an array")
        for loophole in loopholes:
            if not isinstance(loophole, dict):
                raise ValidationError("each prompt loophole must be an object")
            evidence = loophole.get("evidence_case_ids")
            if not isinstance(evidence, list) or not evidence:
                raise ValidationError("each prompt loophole must include evidence_case_ids")
            unknown = set(evidence) - failed_case_ids
            if unknown:
                raise ValidationError(
                    "prompt loophole evidence must reference failed cases: " + ", ".join(sorted(unknown))
                )
        review_suggestions = review.get("optimization_suggestions", [])
        if not isinstance(review_suggestions, list):
            raise ValidationError("review optimization_suggestions must be an array")
        for suggestion in review_suggestions:
            if not isinstance(suggestion, dict) or not str(suggestion.get("guidance", "")).strip():
                raise ValidationError("each optimization suggestion must include non-empty guidance")
            suggestions.append(suggestion)

    missing_reviews = failed_case_ids - reviewed_case_ids
    if missing_reviews:
        raise ValidationError(
            "subagent reviews do not cover all failed cases: " + ", ".join(sorted(missing_reviews))
        )
    if not suggestions:
        raise ValidationError("subagent reviews must include at least one optimization suggestion")
    return suggestions


def _aggregate_eval_summary(result: dict[str, Any]) -> dict[str, Any]:
    summary_keys = (
        "score_report",
        "scorer",
        "pass_rate",
        "passed_count",
        "case_count",
        "scored_count",
        "parse_error_count",
        "unscored_count",
        "json_valid_rate",
        "l2_macro_f1",
        "l3_macro_f1",
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
    if not candidate.get("dev_eval"):
        errors.append(f"{candidate_id}: missing dev_eval metrics")
    if not candidate.get("optimization_suggestions"):
        errors.append(f"{candidate_id}: missing optimization_suggestions")
    _collect_dev_eval_errors(candidate_id, candidate, errors)
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
    if not candidate.get("dev_eval"):
        errors.append(f"{candidate_id}: missing dev_eval metrics")
    if not candidate.get("optimization_suggestions"):
        errors.append(f"{candidate_id}: missing optimization_suggestions")
    _collect_dev_eval_errors(candidate_id, candidate, errors)
    log = logs.get(candidate_id)
    if log is None:
        errors.append(f"{candidate_id}: missing optimization_log entry")
    elif not log.get("metrics") or not log.get("dev_eval"):
        errors.append(f"{candidate_id}: optimization_log entry has null metrics or dev_eval")


def _collect_artifact_errors(
    candidate_id: str,
    candidate: dict[str, Any],
    errors: list[str],
) -> None:
    artifacts = candidate.get("artifact_paths")
    if not isinstance(artifacts, dict):
        errors.append(f"{candidate_id}: missing artifact_paths")
        return
    for key in (*REQUIRED_TRAIN_ARTIFACT_KEYS, "dev_score"):
        value = artifacts.get(key)
        if not value:
            errors.append(f"{candidate_id}: missing artifact path {key}")
            continue
        if not Path(value).exists():
            errors.append(f"{candidate_id}: artifact path does not exist for {key}: {value}")


def _collect_dev_eval_errors(
    candidate_id: str,
    candidate: dict[str, Any],
    errors: list[str],
) -> None:
    dev_eval = candidate.get("dev_eval")
    if not isinstance(dev_eval, dict):
        return
    forbidden = sorted(key for key in EVAL_FORBIDDEN_KEYS if key in dev_eval)
    if forbidden:
        errors.append(f"{candidate_id}: dev_eval contains forbidden keys: {', '.join(forbidden)}")


def _log_records_by_candidate(out_dir: Path) -> dict[str, dict[str, Any]]:
    log_path = out_dir / "optimization_log.jsonl"
    records: dict[str, dict[str, Any]] = {}
    for record in read_jsonl(log_path):
        if record.get("dev_eval") is None and record.get("hidden_eval") is not None:
            record["dev_eval"] = record["hidden_eval"]
        candidate_id = record.get("candidate_id")
        if isinstance(candidate_id, str):
            records[candidate_id] = record
    return records


def _artifact_candidate_ids(out_dir: Path) -> set[str]:
    candidates: set[str] = set()
    patterns = (
        ("dev_score_", ".json"),
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
        if candidate.get("dev_eval", {}).get("pass_rate", 0.0) >= target_pass_rate
    ]
    termination = state.setdefault("termination", {})
    termination["logged_candidate_count"] = logged_count
    if target_reached_candidates:
        selected = max(
            sorted(target_reached_candidates),
            key=lambda current_id: _selection_key(logged[current_id]),
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


def _selection_key(candidate: dict[str, Any]) -> tuple[float, float, float, int]:
    dev_eval = candidate.get("dev_eval") or {}
    prompt_path = Path(candidate["prompt_path"])
    prompt_length = len(prompt_path.read_text(encoding="utf-8")) if prompt_path.exists() else 0
    return (
        float(dev_eval.get("pass_rate") or 0.0),
        float(dev_eval.get("l3_macro_f1") or 0.0),
        float(dev_eval.get("l2_macro_f1") or 0.0),
        -prompt_length,
    )


def _validate_split_group_isolation(datasets: dict[str, Path]) -> None:
    owners: dict[str, str] = {}
    overlaps: set[str] = set()
    for dataset_name, path in datasets.items():
        bundle = normalize_variables_file(path)
        for case in bundle.cases:
            metadata = case.metadata if isinstance(case.metadata, dict) else {}
            split_group = str(metadata["split_group"])
            owner = owners.setdefault(split_group, dataset_name)
            if owner != dataset_name:
                overlaps.add(split_group)
    if overlaps:
        raise ValidationError(
            "split_group values must not cross train/dev/test datasets: "
            + ", ".join(sorted(overlaps))
        )
