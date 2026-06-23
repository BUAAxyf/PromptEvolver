from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import typer

from .config import env_bool, env_float, env_int, load_dotenv_file
from .errors import PromptOptimizerError
from .model import ModelConfig
from .workflow import (
    finalize_best,
    ingest_judgement,
    make_judge_pack,
    optimize_step,
    propose_prompt,
    render_cases,
    run_target_model,
    validate_inputs,
)

app = typer.Typer(
    name="codex-prompt-opt",
    help="Prompt optimization CLI for Codex Judge workflows.",
    no_args_is_help=True,
)


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
    model_name = model or os.environ.get("DSPY_MODEL")
    if not model_name:
        raise typer.BadParameter("model is required via --model or DSPY_MODEL")
    try:
        resolved_temperature = (
            temperature if temperature is not None else env_float("DSPY__TEMPERATURE")
        )
        resolved_max_tokens = max_tokens if max_tokens is not None else env_int("DSPY__MAX_TOKENS")
        resolved_timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else env_float("DSPY__TIMEOUT_SECONDS")
        )
        resolved_enable_thinking = (
            enable_thinking
            if enable_thinking is not None
            else env_bool("EVO_EVAL_ENABLE_THINKING")
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    return ModelConfig(
        model=model_name,
        api_base=api_base or os.environ.get("DSPY_API_BASE"),
        api_key_env=api_key_env,
        temperature=resolved_temperature,
        max_tokens=resolved_max_tokens,
        timeout_seconds=resolved_timeout_seconds,
        enable_thinking=resolved_enable_thinking,
    )


@app.command()
def validate(
    prompt_template: Path = typer.Argument(..., exists=True, readable=True),
    variables_file: Path = typer.Argument(..., exists=True, readable=True),
) -> None:
    """Validate a Mustache prompt template and JSON variables file."""
    _echo_json(validate_inputs(prompt_template, variables_file))


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
def run(
    rendered_cases: Path = typer.Argument(..., exists=True, readable=True),
    out: Path = typer.Option(Path("target_outputs.jsonl"), "--out", "-o"),
    model: str | None = typer.Option(None, "--model"),
    api_base: str | None = typer.Option(None, "--api-base"),
    api_key_env: str = typer.Option("DSPY_API_KEY", "--api-key-env"),
    temperature: float | None = typer.Option(None, "--temperature"),
    max_tokens: int | None = typer.Option(None, "--max-tokens"),
    timeout_seconds: float | None = typer.Option(None, "--timeout-seconds"),
    enable_thinking: bool | None = typer.Option(None, "--enable-thinking/--disable-thinking"),
) -> None:
    """Call the target model through DSPy for all rendered cases."""
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
    """Package rendered prompts and target outputs for Codex Judge."""
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
    workdir: Path = typer.Option(Path(".prompt-opt"), "--workdir"),
    target_pass_rate: float = typer.Option(1.0, "--target-pass-rate"),
    target_average_score_100: float = typer.Option(90.0, "--target-average-score-100"),
) -> None:
    """Ingest Codex Judge scores and failure diagnostics."""
    _echo_json(
        ingest_judgement(
            judgement_file,
            workdir,
            target_pass_rate=target_pass_rate,
            target_average_score_100=target_average_score_100,
        )
    )


@app.command()
def propose(
    prompt_template: Path = typer.Argument(..., exists=True, readable=True),
    judgement_file: Path = typer.Argument(..., exists=True, readable=True),
    out: Path = typer.Option(..., "--out", "-o"),
    workdir: Path | None = typer.Option(Path(".prompt-opt"), "--workdir"),
    candidate_id: str | None = typer.Option(None, "--candidate-id"),
    parent_candidate_id: str | None = typer.Option(None, "--parent-candidate-id"),
    max_failures: int = typer.Option(8, "--max-failures", min=1),
) -> None:
    """Generate the next prompt template from Codex Judge feedback."""
    _echo_json(
        propose_prompt(
            prompt_template,
            judgement_file,
            out,
            workdir=workdir,
            candidate_id=candidate_id,
            parent_candidate_id=parent_candidate_id,
            max_failures=max_failures,
        )
    )


@app.command("optimize-step")
def optimize_step_command(
    prompt_template: Path = typer.Argument(..., exists=True, readable=True),
    variables_file: Path = typer.Argument(..., exists=True, readable=True),
    workdir: Path = typer.Option(Path(".prompt-opt"), "--workdir"),
    candidate_id: str = typer.Option("initial", "--candidate-id"),
    model: str | None = typer.Option(None, "--model"),
    api_base: str | None = typer.Option(None, "--api-base"),
    api_key_env: str = typer.Option("DSPY_API_KEY", "--api-key-env"),
    temperature: float | None = typer.Option(None, "--temperature"),
    max_tokens: int | None = typer.Option(None, "--max-tokens"),
    timeout_seconds: float | None = typer.Option(None, "--timeout-seconds"),
    enable_thinking: bool | None = typer.Option(None, "--enable-thinking/--disable-thinking"),
    target_pass_rate: float = typer.Option(1.0, "--target-pass-rate"),
    target_average_score_100: float = typer.Option(90.0, "--target-average-score-100"),
) -> None:
    """Run one target-model step and emit a judge pack for Codex."""
    _echo_json(
        optimize_step(
            prompt_template,
            variables_file,
            workdir,
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


@app.command()
def finalize(
    workdir: Path = typer.Option(Path(".prompt-opt"), "--workdir"),
    out_dir: Path = typer.Option(Path(".prompt-opt/final"), "--out-dir"),
    target_pass_rate: float = typer.Option(1.0, "--target-pass-rate"),
    target_average_score_100: float = typer.Option(90.0, "--target-average-score-100"),
) -> None:
    """Select the best judged prompt and write final artifacts."""
    _echo_json(
        finalize_best(
            workdir,
            out_dir,
            target_pass_rate=target_pass_rate,
            target_average_score_100=target_average_score_100,
        )
    )


def main() -> None:
    try:
        app()
    except PromptOptimizerError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


if __name__ == "__main__":
    main()
