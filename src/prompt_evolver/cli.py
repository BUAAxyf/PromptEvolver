from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import typer

from .config import (
    MODEL_CONFIG_FIELDS,
    env_bool_value,
    first_use_guidance,
    init_model_config_file,
    load_dotenv_file,
    model_config_status,
    set_model_config_value,
)
from .errors import PromptEvolverError
from .model import ModelConfig
from .prompt_diff_server import (
    DEFAULT_PROMPT_DIFF_PORT,
    create_prompt_diff_server,
    open_prompt_diff_browser,
)
from .strict import (
    strict_blackbox_candidate,
    strict_dev_score_candidate,
    strict_final_eval,
    strict_finalize,
    strict_ingest_candidate,
    strict_init,
    strict_log_candidate,
    strict_train_candidate,
    strict_verify,
)
from .workflow import (
    audit_variables_file,
    blackbox_evaluate,
    finalize_prompt,
    ingest_judgement,
    make_judge_pack,
    optimize_step,
    render_cases,
    run_target_model,
    score_accuracy,
    split_train_test,
    test_step,
    validate_inputs,
)

app = typer.Typer(
    name="prompt-evolver",
    help="Prompt optimization CLI for file-based review workflows.",
    no_args_is_help=True,
)
config_app = typer.Typer(help="View and modify training and evaluator model configuration.")
app.add_typer(config_app, name="config")
strict_app = typer.Typer(help="Run the strict prompt optimization state machine.")
app.add_typer(strict_app, name="strict")


def _echo_json(payload: Any) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


def _model_config(
    model: str | None,
    api_base: str | None,
    api_key_env: str,
    temperature: float | None,
    max_tokens: int | None,
    timeout_seconds: float | None,
    enable_thinking: bool | None,
) -> ModelConfig:
    load_dotenv_file()
    return _prefixed_model_config(
        env_prefix="TRAIN_MODEL",
        model=model,
        api_base=api_base,
        api_key_env=api_key_env,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        enable_thinking=enable_thinking,
        fallback_env_prefixes=("MODEL",),
    )


def _evaluator_model_config(
    model: str | None,
    api_base: str | None,
    api_key_env: str,
    temperature: float | None,
    max_tokens: int | None,
    timeout_seconds: float | None,
    enable_thinking: bool | None,
) -> ModelConfig:
    load_dotenv_file()
    return _prefixed_model_config(
        env_prefix="EVALUATOR_MODEL",
        model=model,
        api_base=api_base,
        api_key_env=api_key_env,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        enable_thinking=enable_thinking,
        fallback_env_prefixes=("TRAIN_MODEL", "MODEL"),
    )


def _prefixed_model_config(
    env_prefix: str,
    model: str | None,
    api_base: str | None,
    api_key_env: str,
    temperature: float | None,
    max_tokens: int | None,
    timeout_seconds: float | None,
    enable_thinking: bool | None,
    fallback_env_prefixes: tuple[str, ...] = (),
) -> ModelConfig:
    def key(suffix: str) -> str:
        return f"{env_prefix}_{suffix}"

    def env_value(suffix: str) -> str | None:
        value = os.environ.get(key(suffix))
        if value not in (None, ""):
            return value
        for fallback_env_prefix in fallback_env_prefixes:
            fallback_value = os.environ.get(f"{fallback_env_prefix}_{suffix}")
            if fallback_value not in (None, ""):
                return fallback_value
        return None

    model_name = model or env_value("NAME")
    if not model_name:
        missing_keys = [key("NAME")]
        if not os.environ.get(api_key_env):
            missing_keys.append(api_key_env)
        guidance = "\n  ".join(first_use_guidance(missing_keys, Path(".env")))
        raise typer.BadParameter(
            f"model is required via --model or {key('NAME')}. "
            f"First-time setup:\n  {guidance}"
        )
    try:
        resolved_temperature = (
            temperature if temperature is not None else _env_float_value(env_value("TEMPERATURE"))
        )
        resolved_max_tokens = max_tokens if max_tokens is not None else _env_int_value(env_value("MAX_TOKENS"))
        resolved_timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else _env_float_value(env_value("TIMEOUT_SECONDS"))
        )
        resolved_enable_thinking = (
            enable_thinking
            if enable_thinking is not None
            else _env_bool_value(env_value("ENABLE_THINKING"), key("ENABLE_THINKING"))
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    resolved_api_key_env = api_key_env
    if not os.environ.get(resolved_api_key_env):
        for fallback_env_prefix in fallback_env_prefixes:
            fallback_api_key = f"{fallback_env_prefix}_API_KEY"
            if not os.environ.get(fallback_api_key):
                continue
            resolved_api_key_env = fallback_api_key
            break
    return ModelConfig(
        model=model_name,
        api_base=api_base or env_value("API_BASE"),
        api_key_env=resolved_api_key_env,
        temperature=resolved_temperature,
        max_tokens=resolved_max_tokens,
        timeout_seconds=resolved_timeout_seconds,
        enable_thinking=resolved_enable_thinking,
    )


def _env_float_value(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _env_int_value(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _env_bool_value(value: str | None, name: str) -> bool | None:
    if value in (None, ""):
        return None
    return env_bool_value(value, name)


@config_app.command("show")
def config_show(
    env_file: Path = typer.Option(Path(".env"), "--env-file"),
    reveal_secrets: bool = typer.Option(False, "--reveal-secrets"),
) -> None:
    """Show model configuration, redacting secrets by default."""
    _echo_json(model_config_status(env_file, reveal_secrets=reveal_secrets))


@config_app.command("init")
def config_init(
    env_file: Path = typer.Option(Path(".env"), "--env-file"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Create a local model configuration file with placeholders."""
    created = init_model_config_file(env_file, force=force)
    status = model_config_status(env_file)
    _echo_json(
        {
            "env_file": str(env_file),
            "created": created,
            "overwritten": created and force,
            "message": "created configuration file"
            if created
            else "configuration file already exists; pass --force to overwrite",
            "next_steps": status["next_steps"],
        }
    )


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help=f"One of: {', '.join(MODEL_CONFIG_FIELDS)}"),
    value: str = typer.Argument(...),
    env_file: Path = typer.Option(Path(".env"), "--env-file"),
) -> None:
    """Set one model configuration value in the env file."""
    try:
        set_model_config_value(env_file, key, value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    _echo_json(
        {
            "env_file": str(env_file),
            "updated": key,
            "status": model_config_status(env_file)["values"].get(key, ""),
        }
    )


@strict_app.command("init")
def strict_init_command(
    variables_file: Path = typer.Argument(..., exists=True, readable=True),
    prompt_template: Path = typer.Option(..., "--prompt", exists=True, readable=True),
    out_dir: Path = typer.Option(..., "--out-dir"),
    max_iterations: int = typer.Option(100, "--max-iterations"),
    target_pass_rate: float = typer.Option(0.95, "--target-pass-rate"),
    train_ratio: float = typer.Option(0.7, "--train-ratio"),
    seed: int = typer.Option(13, "--seed"),
    target_average_score_100: float = typer.Option(95.0, "--target-average-score-100"),
    train_json: Path | None = typer.Option(None, "--train-json", exists=True, readable=True),
    dev_json: Path | None = typer.Option(None, "--dev-json", exists=True, readable=True),
    test_json: Path | None = typer.Option(None, "--test-json", exists=True, readable=True),
) -> None:
    """Initialize a strict trace with explicit train/dev/test or a legacy two-way split."""
    _echo_json(
        strict_init(
            variables_file,
            prompt_template,
            out_dir,
            max_iterations=max_iterations,
            target_pass_rate=target_pass_rate,
            train_ratio=train_ratio,
            seed=seed,
            target_average_score_100=target_average_score_100,
            train_json=train_json,
            dev_json=dev_json,
            test_json=test_json,
        )
    )


@strict_app.command("train-candidate")
def strict_train_candidate_command(
    source_prompt: Path = typer.Argument(..., exists=True, readable=True),
    out_dir: Path = typer.Option(..., "--out-dir"),
    candidate_id: str = typer.Option(..., "--candidate-id"),
    parent_candidate_id: str | None = typer.Option(None, "--parent-candidate-id"),
    strategy: str = typer.Option("", "--strategy"),
    model: str | None = typer.Option(None, "--model"),
    api_base: str | None = typer.Option(None, "--api-base"),
    api_key_env: str = typer.Option("TRAIN_MODEL_API_KEY", "--api-key-env"),
    temperature: float | None = typer.Option(None, "--temperature"),
    max_tokens: int | None = typer.Option(None, "--max-tokens"),
    timeout_seconds: float | None = typer.Option(None, "--timeout-seconds"),
    enable_thinking: bool | None = typer.Option(None, "--enable-thinking/--disable-thinking"),
) -> None:
    """Run the training-set step for one strict candidate."""
    _echo_json(
        strict_train_candidate(
            source_prompt,
            out_dir,
            candidate_id,
            _model_config(
                model,
                api_base,
                api_key_env,
                temperature,
                max_tokens,
                timeout_seconds,
                enable_thinking,
            ),
            parent_candidate_id=parent_candidate_id,
            strategy=strategy,
        )
    )


@strict_app.command("ingest-candidate")
def strict_ingest_candidate_command(
    judgement_file: Path = typer.Argument(..., exists=True, readable=True),
    out_dir: Path = typer.Option(..., "--out-dir"),
    candidate_id: str = typer.Option(..., "--candidate-id"),
    subagent_reviews: Path = typer.Option(..., "--subagent-reviews", exists=True, readable=True),
) -> None:
    """Ingest reviewed training judgement for one strict candidate."""
    _echo_json(strict_ingest_candidate(judgement_file, out_dir, candidate_id, subagent_reviews))


@strict_app.command("blackbox-candidate")
def strict_blackbox_candidate_command(
    out_dir: Path = typer.Option(..., "--out-dir"),
    candidate_id: str = typer.Option(..., "--candidate-id"),
    model: str | None = typer.Option(None, "--model", help="Target model override."),
    api_base: str | None = typer.Option(None, "--api-base"),
    api_key_env: str = typer.Option("TRAIN_MODEL_API_KEY", "--api-key-env"),
    temperature: float | None = typer.Option(None, "--temperature"),
    max_tokens: int | None = typer.Option(None, "--max-tokens"),
    timeout_seconds: float | None = typer.Option(None, "--timeout-seconds"),
    enable_thinking: bool | None = typer.Option(None, "--enable-thinking/--disable-thinking"),
    evaluator_model: str | None = typer.Option(None, "--evaluator-model"),
    evaluator_api_base: str | None = typer.Option(None, "--evaluator-api-base"),
    evaluator_api_key_env: str = typer.Option(
        "EVALUATOR_MODEL_API_KEY",
        "--evaluator-api-key-env",
    ),
    evaluator_temperature: float | None = typer.Option(None, "--evaluator-temperature"),
    evaluator_max_tokens: int | None = typer.Option(None, "--evaluator-max-tokens"),
    evaluator_timeout_seconds: float | None = typer.Option(
        None,
        "--evaluator-timeout-seconds",
    ),
    evaluator_enable_thinking: bool | None = typer.Option(
        None,
        "--evaluator-enable-thinking/--evaluator-disable-thinking",
    ),
) -> None:
    """Compatibility command for LLM-scored development evaluation."""
    _echo_json(
        strict_blackbox_candidate(
            out_dir,
            candidate_id,
            _model_config(
                model,
                api_base,
                api_key_env,
                temperature,
                max_tokens,
                timeout_seconds,
                enable_thinking,
            ),
            _evaluator_model_config(
                evaluator_model,
                evaluator_api_base,
                evaluator_api_key_env,
                evaluator_temperature,
                evaluator_max_tokens,
                evaluator_timeout_seconds,
                evaluator_enable_thinking,
            ),
        )
    )


@strict_app.command("dev-score-candidate")
def strict_dev_score_candidate_command(
    out_dir: Path = typer.Option(..., "--out-dir"),
    candidate_id: str = typer.Option(..., "--candidate-id"),
    scorer: str = typer.Option("exact", "--scorer", help="exact or llm"),
    model: str | None = typer.Option(None, "--model", help="Target model override."),
    api_base: str | None = typer.Option(None, "--api-base"),
    api_key_env: str = typer.Option("TRAIN_MODEL_API_KEY", "--api-key-env"),
    temperature: float | None = typer.Option(None, "--temperature"),
    max_tokens: int | None = typer.Option(None, "--max-tokens"),
    timeout_seconds: float | None = typer.Option(None, "--timeout-seconds"),
    enable_thinking: bool | None = typer.Option(None, "--enable-thinking/--disable-thinking"),
    evaluator_model: str | None = typer.Option(None, "--evaluator-model"),
    evaluator_api_base: str | None = typer.Option(None, "--evaluator-api-base"),
    evaluator_api_key_env: str = typer.Option(
        "EVALUATOR_MODEL_API_KEY",
        "--evaluator-api-key-env",
    ),
    evaluator_temperature: float | None = typer.Option(None, "--evaluator-temperature"),
    evaluator_max_tokens: int | None = typer.Option(None, "--evaluator-max-tokens"),
    evaluator_timeout_seconds: float | None = typer.Option(
        None,
        "--evaluator-timeout-seconds",
    ),
    evaluator_enable_thinking: bool | None = typer.Option(
        None,
        "--evaluator-enable-thinking/--evaluator-disable-thinking",
    ),
) -> None:
    """Score one strict candidate on the development set."""
    evaluator_config = None
    if scorer == "llm":
        evaluator_config = _evaluator_model_config(
            evaluator_model,
            evaluator_api_base,
            evaluator_api_key_env,
            evaluator_temperature,
            evaluator_max_tokens,
            evaluator_timeout_seconds,
            evaluator_enable_thinking,
        )
    _echo_json(
        strict_dev_score_candidate(
            out_dir,
            candidate_id,
            _model_config(
                model,
                api_base,
                api_key_env,
                temperature,
                max_tokens,
                timeout_seconds,
                enable_thinking,
            ),
            evaluator_config,
            scorer=scorer,
        )
    )


@strict_app.command("log-candidate")
def strict_log_candidate_command(
    out_dir: Path = typer.Option(..., "--out-dir"),
    candidate_id: str = typer.Option(..., "--candidate-id"),
) -> None:
    """Append a complete strict candidate to the optimization log."""
    _echo_json(strict_log_candidate(out_dir, candidate_id))


@strict_app.command("verify")
def strict_verify_command(
    out_dir: Path = typer.Option(..., "--out-dir"),
) -> None:
    """Audit strict trace completeness."""
    report = strict_verify(out_dir)
    _echo_json(report)
    if not report["valid"]:
        raise typer.Exit(code=1)


@strict_app.command("final-eval")
def strict_final_eval_command(
    out_dir: Path = typer.Option(..., "--out-dir"),
    candidate_id: str = typer.Option(..., "--candidate-id"),
    model: str | None = typer.Option(None, "--model"),
    api_base: str | None = typer.Option(None, "--api-base"),
    api_key_env: str = typer.Option("TRAIN_MODEL_API_KEY", "--api-key-env"),
    temperature: float | None = typer.Option(None, "--temperature"),
    max_tokens: int | None = typer.Option(None, "--max-tokens"),
    timeout_seconds: float | None = typer.Option(None, "--timeout-seconds"),
    enable_thinking: bool | None = typer.Option(None, "--enable-thinking/--disable-thinking"),
) -> None:
    """Run the selected candidate on the final test set exactly once."""
    _echo_json(
        strict_final_eval(
            out_dir,
            candidate_id,
            _model_config(
                model,
                api_base,
                api_key_env,
                temperature,
                max_tokens,
                timeout_seconds,
                enable_thinking,
            ),
        )
    )


@strict_app.command("finalize")
def strict_finalize_command(
    out_dir: Path = typer.Option(..., "--out-dir"),
    candidate_id: str | None = typer.Option(None, "--candidate-id"),
) -> None:
    """Verify strict trace and write final artifacts."""
    _echo_json(strict_finalize(out_dir, candidate_id))


@app.command()
def validate(
    prompt_template: Path = typer.Argument(..., exists=True, readable=True),
    variables_file: Path = typer.Argument(..., exists=True, readable=True),
) -> None:
    """Validate a Mustache prompt template and JSON variables file."""
    _echo_json(validate_inputs(prompt_template, variables_file))


@app.command("data-audit")
def data_audit(
    variables_file: Path = typer.Argument(..., exists=True, readable=True),
) -> None:
    """Audit labels, split groups, adjudication status, and duplicate conflicts."""
    _echo_json(audit_variables_file(variables_file))


@app.command()
def render(
    prompt_template: Path = typer.Argument(..., exists=True, readable=True),
    variables_file: Path = typer.Argument(..., exists=True, readable=True),
    out: Path = typer.Option(Path("rendered_cases.jsonl"), "--out", "-o"),
    candidate_id: str = typer.Option("initial", "--candidate-id"),
) -> None:
    """Render each JSON case into a full task instance."""
    records = render_cases(prompt_template, variables_file, out, candidate_id)
    _echo_json({"out": str(out), "case_count": len(records), "candidate_id": candidate_id})


@app.command()
def split(
    variables_file: Path = typer.Argument(..., exists=True, readable=True),
    train_out: Path = typer.Option(Path(".prompt-evolver/train.json"), "--train-out"),
    test_out: Path = typer.Option(Path(".prompt-evolver/test.json"), "--test-out"),
    train_ratio: float = typer.Option(0.7, "--train-ratio"),
    seed: int = typer.Option(13, "--seed"),
) -> None:
    """Split variables JSON into stratified train and test sets."""
    train_out.parent.mkdir(parents=True, exist_ok=True)
    test_out.parent.mkdir(parents=True, exist_ok=True)
    _echo_json(split_train_test(variables_file, train_out, test_out, train_ratio, seed))


@app.command()
def run(
    rendered_cases: Path = typer.Argument(..., exists=True, readable=True),
    out: Path = typer.Option(Path("target_outputs.jsonl"), "--out", "-o"),
    model: str | None = typer.Option(None, "--model"),
    api_base: str | None = typer.Option(None, "--api-base"),
    api_key_env: str = typer.Option("TRAIN_MODEL_API_KEY", "--api-key-env"),
    temperature: float | None = typer.Option(None, "--temperature"),
    max_tokens: int | None = typer.Option(None, "--max-tokens"),
    timeout_seconds: float | None = typer.Option(None, "--timeout-seconds"),
    enable_thinking: bool | None = typer.Option(None, "--enable-thinking/--disable-thinking"),
) -> None:
    """Call the target model for all rendered cases."""
    records = run_target_model(
        rendered_cases,
        out,
        _model_config(
            model,
            api_base,
            api_key_env,
            temperature,
            max_tokens,
            timeout_seconds,
            enable_thinking,
        ),
    )
    _echo_json({"out": str(out), "case_count": len(records)})


@app.command("judge-pack")
def judge_pack(
    rendered_cases: Path = typer.Argument(..., exists=True, readable=True),
    target_outputs: Path = typer.Argument(..., exists=True, readable=True),
    variables_file: Path = typer.Argument(..., exists=True, readable=True),
    out: Path = typer.Option(Path("judge_pack.json"), "--out", "-o"),
    target_pass_rate: float = typer.Option(1.0, "--target-pass-rate"),
    target_average_score_100: float = typer.Option(90.0, "--target-average-score-100"),
) -> None:
    """Package rendered prompts and target outputs for structured review."""
    pack = make_judge_pack(
        rendered_cases,
        target_outputs,
        variables_file,
        out,
        target_pass_rate=target_pass_rate,
        target_average_score_100=target_average_score_100,
    )
    _echo_json({"out": str(out), "case_count": len(pack["cases"])})


@app.command("ingest-judgement")
def ingest_judgement_command(
    judgement_file: Path = typer.Argument(..., exists=True, readable=True),
    out: Path | None = typer.Option(None, "--out", "-o"),
    out_dir: Path | None = typer.Option(None, "--out-dir"),
    target_pass_rate: float = typer.Option(1.0, "--target-pass-rate"),
    target_average_score_100: float = typer.Option(90.0, "--target-average-score-100"),
) -> None:
    """Compute metrics for review scores and optionally write enriched judgement JSON."""
    _echo_json(
        ingest_judgement(
            judgement_file,
            out=out,
            out_dir=out_dir,
            target_pass_rate=target_pass_rate,
            target_average_score_100=target_average_score_100,
        )
    )


@app.command("score-accuracy")
def score_accuracy_command(
    variables_file: Path = typer.Argument(..., exists=True, readable=True),
    target_outputs: Path = typer.Argument(..., exists=True, readable=True),
    out: Path | None = typer.Option(None, "--out", "-o"),
) -> None:
    """Score target outputs against expected ground truth."""
    _echo_json(score_accuracy(variables_file, target_outputs, out))


@app.command("optimize-step")
def optimize_step_command(
    prompt_template: Path = typer.Argument(..., exists=True, readable=True),
    variables_file: Path = typer.Argument(..., exists=True, readable=True),
    out_dir: Path = typer.Option(
        Path(".prompt-evolver"),
        "--out-dir",
        help="Directory for rendered cases, target outputs, and judge pack.",
    ),
    candidate_id: str = typer.Option("initial", "--candidate-id"),
    model: str | None = typer.Option(None, "--model"),
    api_base: str | None = typer.Option(None, "--api-base"),
    api_key_env: str = typer.Option("TRAIN_MODEL_API_KEY", "--api-key-env"),
    temperature: float | None = typer.Option(None, "--temperature"),
    max_tokens: int | None = typer.Option(None, "--max-tokens"),
    timeout_seconds: float | None = typer.Option(None, "--timeout-seconds"),
    enable_thinking: bool | None = typer.Option(None, "--enable-thinking/--disable-thinking"),
    target_pass_rate: float = typer.Option(1.0, "--target-pass-rate"),
    target_average_score_100: float = typer.Option(90.0, "--target-average-score-100"),
) -> None:
    """Run one target-model step and emit a judge pack for structured review."""
    _echo_json(
        optimize_step(
            prompt_template,
            variables_file,
            out_dir,
            candidate_id,
            _model_config(
                model,
                api_base,
                api_key_env,
                temperature,
                max_tokens,
                timeout_seconds,
                enable_thinking,
            ),
            target_pass_rate=target_pass_rate,
            target_average_score_100=target_average_score_100,
        )
    )


@app.command("test-step")
def test_step_command(
    prompt_template: Path = typer.Argument(..., exists=True, readable=True),
    variables_file: Path = typer.Argument(..., exists=True, readable=True),
    out_dir: Path = typer.Option(
        Path(".prompt-evolver"),
        "--out-dir",
        help="Directory for rendered cases, target outputs, and accuracy report.",
    ),
    candidate_id: str = typer.Option("final_test", "--candidate-id"),
    model: str | None = typer.Option(None, "--model"),
    api_base: str | None = typer.Option(None, "--api-base"),
    api_key_env: str = typer.Option("TRAIN_MODEL_API_KEY", "--api-key-env"),
    temperature: float | None = typer.Option(None, "--temperature"),
    max_tokens: int | None = typer.Option(None, "--max-tokens"),
    timeout_seconds: float | None = typer.Option(None, "--timeout-seconds"),
    enable_thinking: bool | None = typer.Option(None, "--enable-thinking/--disable-thinking"),
) -> None:
    """Run one final test-set target-model step and score accuracy."""
    _echo_json(
        test_step(
            prompt_template,
            variables_file,
            out_dir,
            candidate_id,
            _model_config(
                model,
                api_base,
                api_key_env,
                temperature,
                max_tokens,
                timeout_seconds,
                enable_thinking,
            ),
        )
    )


@app.command("blackbox-eval")
def blackbox_eval_command(
    prompt_template: Path = typer.Argument(..., exists=True, readable=True),
    variables_file: Path = typer.Argument(..., exists=True, readable=True),
    out_dir: Path = typer.Option(
        Path(".prompt-evolver"),
        "--out-dir",
        help="Directory for the aggregate black-box score report.",
    ),
    candidate_id: str = typer.Option("blackbox_eval", "--candidate-id"),
    model: str | None = typer.Option(None, "--model", help="Target model override."),
    api_base: str | None = typer.Option(None, "--api-base"),
    api_key_env: str = typer.Option("TRAIN_MODEL_API_KEY", "--api-key-env"),
    temperature: float | None = typer.Option(None, "--temperature"),
    max_tokens: int | None = typer.Option(None, "--max-tokens"),
    timeout_seconds: float | None = typer.Option(None, "--timeout-seconds"),
    enable_thinking: bool | None = typer.Option(None, "--enable-thinking/--disable-thinking"),
    evaluator_model: str | None = typer.Option(None, "--evaluator-model"),
    evaluator_api_base: str | None = typer.Option(None, "--evaluator-api-base"),
    evaluator_api_key_env: str = typer.Option(
        "EVALUATOR_MODEL_API_KEY",
        "--evaluator-api-key-env",
    ),
    evaluator_temperature: float | None = typer.Option(None, "--evaluator-temperature"),
    evaluator_max_tokens: int | None = typer.Option(None, "--evaluator-max-tokens"),
    evaluator_timeout_seconds: float | None = typer.Option(
        None,
        "--evaluator-timeout-seconds",
    ),
    evaluator_enable_thinking: bool | None = typer.Option(
        None,
        "--evaluator-enable-thinking/--evaluator-disable-thinking",
    ),
) -> None:
    """Run a private black-box evaluation and write only aggregate scores."""
    _echo_json(
        blackbox_evaluate(
            prompt_template,
            variables_file,
            out_dir,
            candidate_id,
            _model_config(
                model,
                api_base,
                api_key_env,
                temperature,
                max_tokens,
                timeout_seconds,
                enable_thinking,
            ),
            _evaluator_model_config(
                evaluator_model,
                evaluator_api_base,
                evaluator_api_key_env,
                evaluator_temperature,
                evaluator_max_tokens,
                evaluator_timeout_seconds,
                evaluator_enable_thinking,
            ),
        )
    )


@app.command()
def finalize(
    prompt_template: Path = typer.Argument(..., exists=True, readable=True),
    judgement_file: Path = typer.Argument(..., exists=True, readable=True),
    out_dir: Path = typer.Option(Path(".prompt-evolver/final"), "--out-dir"),
    target_pass_rate: float = typer.Option(1.0, "--target-pass-rate"),
    target_average_score_100: float = typer.Option(90.0, "--target-average-score-100"),
) -> None:
    """Write final artifacts for the selected prompt."""
    _echo_json(
        finalize_prompt(
            prompt_template,
            judgement_file,
            out_dir,
            target_pass_rate=target_pass_rate,
            target_average_score_100=target_average_score_100,
        )
    )


@app.command("prompt-diff")
def prompt_diff_command(
    original_prompt: Path = typer.Argument(..., exists=True, readable=True),
    revised_prompt: Path = typer.Argument(..., exists=True, readable=True),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(DEFAULT_PROMPT_DIFF_PORT, "--port"),
    open_browser: bool = typer.Option(True, "--open-browser/--no-open-browser"),
) -> None:
    """Open a local browser UI for side-by-side prompt Markdown diff review."""
    server, info = create_prompt_diff_server(original_prompt, revised_prompt, host, port)
    typer.echo("Prompt diff viewer is running.")
    typer.echo(f"URL: {info.url}")
    typer.echo(f"Original prompt: {info.original_prompt}")
    typer.echo(f"Revised prompt: {info.revised_prompt}")
    typer.echo("Review the diff in your browser. Press Ctrl+C in this terminal to stop the server.")
    if open_browser:
        opened = open_prompt_diff_browser(info.url)
        typer.echo("Browser open requested." if opened else "Browser auto-open failed; open the URL manually.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        typer.echo("\nPrompt diff viewer stopped.")
    finally:
        server.server_close()


def main() -> None:
    try:
        app()
    except PromptEvolverError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


if __name__ == "__main__":
    main()
