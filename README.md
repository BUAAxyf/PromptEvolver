# Prompt Evolver

Chinese documentation: [README_CN.md](README_CN.md)

Prompt Evolver is a local prompt optimization toolkit for file-based prompt evaluation workflows. The CLI runs repeatable automation: it validates a Mustache prompt template, renders JSON cases into task instances, calls the configured target model, packages target outputs for structured review, ingests structured judgements, and writes final artifacts for the selected prompt.

The CLI is responsible for deterministic file and model-execution steps. Prompt rewriting and judgement happen outside the CLI by reading the generated judge pack and writing the expected JSON contracts.

## Core Capabilities

- Render a Mustache prompt template with a single JSON file containing multiple evaluation cases.
- Treat each rendered prompt as a task instance, also called a rendered prompt, prompt instantiation, or evaluation case/example.
- Split one variables file into deterministic train and test files, stratified by `expected.ground_truth` with a default 70% / 30% ratio.
- Call the configured target model in `run`, `optimize-step`, and `blackbox-eval`.
- Run an independent evaluator model in `blackbox-eval` to score a private evaluation set without persisting case-level content.
- Exchange structured judgement results through JSON files instead of coupling review to the CLI.
- Keep prompt generation outside the CLI; edit the prompt template from review findings between evaluation steps.
- Optimize only the prompt template; the CLI never rewrites the variables file or appends bad cases to prompts.
- Keep bad-case analysis on the training set and use hidden evaluation scores as a black-box optimization signal each iteration.
- Open a local side-by-side prompt diff review page with `prompt-diff` after prompt iteration.
- Support stopping by threshold and budget: target pass rate, target average `score_100`, and max iteration budget.

## Environment Requirements

- macOS or another Unix-like shell environment.
- Python 3.10 or newer.
- Target model configuration for real model runs.

## Installation

Install the package in editable mode:

```bash
python3 -m pip install -e .
```

If you create a virtual environment, activate it before installing:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

If the environment directory is named `venv`, activate it with:

```bash
source venv/bin/activate
```

## Configuration

The CLI reads target model settings from command options or environment variables:

- `MODEL_NAME`: target model identifier.
- `MODEL_API_BASE`: optional API base URL.
- `MODEL_API_KEY`: default API key environment variable.
- `MODEL_TEMPERATURE`: optional target-model temperature.
- `MODEL_MAX_TOKENS`: optional max token budget.
- `MODEL_TIMEOUT_SECONDS`: optional target-model request timeout.
- `MODEL_ENABLE_THINKING`: optional boolean passed to compatible OpenAI-style backends as `extra_body.enable_thinking`.

`blackbox-eval` also supports an independent evaluator model. Leave these values blank to reuse the matching `MODEL_*` target-model values:

- `EVALUATOR_MODEL_NAME`: evaluator model identifier.
- `EVALUATOR_MODEL_API_BASE`: optional evaluator API base URL.
- `EVALUATOR_MODEL_API_KEY`: optional evaluator API key. Falls back to `MODEL_API_KEY` when blank.
- `EVALUATOR_MODEL_TEMPERATURE`: optional evaluator temperature.
- `EVALUATOR_MODEL_MAX_TOKENS`: optional evaluator max token budget.
- `EVALUATOR_MODEL_TIMEOUT_SECONDS`: optional evaluator request timeout.
- `EVALUATOR_MODEL_ENABLE_THINKING`: optional evaluator thinking flag.

The CLI automatically reads `.env` from the current working directory before model execution. The `.env*` files in this repository contain placeholders only. Keep real secrets local.

When `MODEL_API_BASE` is set and `MODEL_NAME` has no provider prefix, the CLI treats it as an OpenAI-compatible model and uses `openai/<MODEL_NAME>` internally.

View current model configuration:

```bash
prompt-evolver config show
```

The output includes `evaluator_fallbacks` so you can see which blank `EVALUATOR_MODEL_*` fields currently reuse `MODEL_*` values.

Create a first-use local config file:

```bash
prompt-evolver config init
```

Update a model parameter:

```bash
prompt-evolver config set MODEL_NAME DeepSeek-V4-Pro
prompt-evolver config set MODEL_API_BASE https://example.com/v1
prompt-evolver config set MODEL_API_KEY sk-...
prompt-evolver config set MODEL_TEMPERATURE 0.1
prompt-evolver config set MODEL_MAX_TOKENS 2048
prompt-evolver config set MODEL_TIMEOUT_SECONDS 90
prompt-evolver config set MODEL_ENABLE_THINKING true

# Optional: set these only when the evaluator should differ from the target model.
prompt-evolver config set EVALUATOR_MODEL_NAME DeepSeek-V4-Pro
prompt-evolver config set EVALUATOR_MODEL_API_BASE https://example.com/v1
prompt-evolver config set EVALUATOR_MODEL_API_KEY sk-...
```

## Input Format

Prompt templates use Mustache syntax:

```mustache
Classify the support request.

Request: {{request}}

Return JSON with fields: label, confidence, rationale.
```

The variables file is one JSON file with multiple cases:

```json
{
  "task": {
    "name": "Support classifier",
    "rubric": "The label must match expected.label and the response must be valid JSON."
  },
  "cases": [
    {
      "id": "refund_001",
      "variables": {
        "request": "I was charged twice and need a refund."
      },
      "expected": {
        "label": "billing"
      }
    }
  ]
}
```

## CLI Workflow

When no explicit train/test files are provided, create the default split first. The split is deterministic, uses `--train-ratio 0.7`, and stratifies by `expected.ground_truth`:

```bash
prompt-evolver split examples/task.example.json --train-out .prompt-evolver/train.json --test-out .prompt-evolver/test.json
```

Validate the checked-in sample prompt against the training set:

```bash
prompt-evolver validate examples/prompt.example.md .prompt-evolver/train.json
```

The generated test file is used as a hidden evaluation set during optimization. It may be format-validated, but test cases and expected outputs should not be opened, summarized, or used for bad-case analysis:

```bash
prompt-evolver validate examples/prompt.example.md .prompt-evolver/test.json
```

Render training task instances:

```bash
prompt-evolver render examples/prompt.example.md .prompt-evolver/train.json --out .prompt-evolver/rendered_cases.jsonl
```

Run the target model on the training set:

```bash
prompt-evolver run .prompt-evolver/rendered_cases.jsonl --out .prompt-evolver/target_outputs.jsonl --model "$MODEL_NAME"
```

Package materials for structured review:

```bash
prompt-evolver judge-pack .prompt-evolver/rendered_cases.jsonl .prompt-evolver/target_outputs.jsonl .prompt-evolver/train.json --out .prompt-evolver/judge_pack.json
```

After `judgement.json` is written, ingest it:

```bash
prompt-evolver ingest-judgement .prompt-evolver/judgement.json --out-dir .prompt-evolver
```

Run one automated target-model step and produce a judge pack:

```bash
prompt-evolver optimize-step examples/prompt.example.md .prompt-evolver/train.json --out-dir .prompt-evolver --candidate-id initial --model "$MODEL_NAME"
```

The checked-in sample files are `examples/prompt.example.md` and `examples/task.example.json`. Local working inputs named `examples/prompt.md` and `examples/task.json` are ignored so real prompts and evaluation data can stay private.

The CLI does not generate the next prompt. Use review findings to edit the prompt template, record the iteration in `.prompt-evolver/optimization_log.jsonl`, and then run `optimize-step` again with the new prompt.

After each candidate prompt is produced, score the hidden evaluation set with the independent evaluator:

```bash
prompt-evolver blackbox-eval .prompt-evolver/prompts/candidate_001.md .prompt-evolver/test.json --out-dir .prompt-evolver --candidate-id candidate_001
```

`blackbox-eval` renders each hidden case in memory, calls the target model, asks the evaluator model to compare the target output with the expected ground truth, and writes only `blackbox_score_<candidate_id>.json`. It does not persist case IDs, rendered prompts, target outputs, judge prompts, or per-case scores.

Finalize the selected prompt:

```bash
prompt-evolver finalize .prompt-evolver/prompts/best.md .prompt-evolver/judgement_best.json --out-dir .prompt-evolver/final
```

After training reaches the stopping criteria or the iteration budget is exhausted, `test-step` is still available for a final file-based accuracy audit:

```bash
prompt-evolver test-step .prompt-evolver/final/best_prompt.md .prompt-evolver/test.json --out-dir .prompt-evolver --candidate-id final_test --model "$MODEL_NAME"
```

If target outputs already exist, score them directly:

```bash
prompt-evolver score-accuracy .prompt-evolver/test.json .prompt-evolver/target_outputs_final_test.jsonl --out .prompt-evolver/accuracy_final_test.json
```

After prompt iteration, open a browser review page that compares the input prompt with the final prompt:

```bash
prompt-evolver prompt-diff examples/prompt.md output/trace_1782302086/final/best_prompt.md
```

The command starts a foreground local server from the packaged Prompt Evolver UI assets, opens the browser when possible, prints the review URL, and stops when you press `Ctrl+C`. If the default port is occupied, the CLI automatically tries the next available port.

## Skill Usage

The Skill lives in `skills/prompt-evolver`. It provides a repeatable workflow around the CLI: input validation, one-step target-model evaluation, judge-pack review, prompt iteration, and finalization.

Use these short prompts as starting points:

- Dataset split: `Use $prompt-evolver to split examples/task.example.json into train and test files with the default stratified 70/30 method.`
- Input validation: `Use $prompt-evolver to validate examples/prompt.example.md against .prompt-evolver/train.json before any training model call.`
- One evaluation step: `Use $prompt-evolver to run one optimize-step for examples/prompt.example.md and .prompt-evolver/train.json, then save the judge pack under .prompt-evolver.`
- Review outputs: `Use $prompt-evolver to review the judge pack, score each case, and write judgement JSON in the expected schema.`
- Hidden evaluation: `Use $prompt-evolver to run blackbox-eval for the current prompt and .prompt-evolver/test.json, then use only the aggregate score as an optimization signal.`
- Improve the prompt: `Use $prompt-evolver to summarize failing cases, update the prompt template, and record the iteration in .prompt-evolver/optimization_log.jsonl.`
- Finalize: `Use $prompt-evolver to finalize the selected prompt and judgement into .prompt-evolver/final.`
- Final file audit: `Use $prompt-evolver to run test-step once on .prompt-evolver/test.json and report .prompt-evolver/accuracy_final_test.json.`
- Prompt diff review: `Use prompt-evolver prompt-diff examples/prompt.md output/trace_1782302086/final/best_prompt.md, then ask the user to open the printed URL and review the side-by-side prompt diff.`

## Documentation Maintenance

Keep `README.md` and `README_CN.md` semantically aligned. Any future update to one README should include the same user-facing change in the other README in the same task.

## Directory Structure

```text
src/prompt_evolver/        CLI implementation
src/prompt_evolver/static/ Packaged browser UI assets
tests/                     Unit tests
examples/prompt.example.md Minimal checked-in prompt example
examples/task.example.json Minimal checked-in JSON variables example
examples/prompt_jxb_v*.md  JXB prompt iteration history
skills/prompt-evolver/     Skill for this workflow
README.md                  English documentation
README_CN.md               Chinese documentation
PLAN.md                    Original design plan
```

## Development And Testing

Run the unit tests without external model calls:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

Run a syntax compile check:

```bash
python3 -m compileall src tests
```

For real model execution, configure `MODEL_NAME` and credentials first.
