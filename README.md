# Codex Prompt Optimizer

Codex Prompt Optimizer is a local prompt optimization toolkit for a Codex-led workflow. The CLI runs repeatable automation: it validates a Mustache prompt template, renders JSON cases into task instances, calls the target model through DSPy, packages target outputs for Codex Judge review, ingests structured judgements, proposes the next prompt template, and finalizes the best prompt.

Codex is not the target model executor. Codex reads the target model outputs, acts as Judge, writes structured scores and failure analysis, and invokes the CLI for the next optimization step.

## Core Capabilities

- Render a Mustache prompt template with a single JSON file containing multiple evaluation cases.
- Treat each rendered prompt as a task instance, also called a rendered prompt, prompt instantiation, or evaluation case/example.
- Call the target model with DSPy in `run` and `optimize-step`.
- Exchange Codex Judge results through JSON files instead of calling Codex from the CLI.
- Optimize only the prompt template; the CLI never rewrites the variables file.
- Stop by threshold and budget: target pass rate, target average `score_100`, and max iteration budget.

## Environment Requirements

- macOS or another Unix-like shell environment.
- Python 3.10 or newer.
- A DSPy-compatible model configuration for real target model runs.

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

The CLI reads model settings from command options or environment variables:

- `DSPY_MODEL`: target model identifier passed to `dspy.LM`.
- `DSPY_API_BASE`: optional API base URL.
- `DSPY_API_KEY`: default API key environment variable.
- `DSPY__TEMPERATURE`: optional target-model temperature.
- `DSPY__MAX_TOKENS`: optional max token budget.
- `DSPY__TIMEOUT_SECONDS`: optional target-model request timeout.
- `EVO_EVAL_ENABLE_THINKING`: optional boolean passed to compatible OpenAI-style backends as `extra_body.enable_thinking`.

The CLI automatically reads `.env` from the current working directory before target model execution. The `.env*` files in this repository contain placeholders only. Keep real secrets local.

When `DSPY_API_BASE` is set and `DSPY_MODEL` has no provider prefix, the CLI treats it as an OpenAI-compatible model and passes `openai/<DSPY_MODEL>` to DSPy/LiteLLM.

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

Validate inputs:

```bash
codex-prompt-opt validate examples/prompt.md examples/task.json
```

Render task instances:

```bash
codex-prompt-opt render examples/prompt.md examples/task.json --out .prompt-opt/rendered_cases.jsonl
```

Run the target model:

```bash
codex-prompt-opt run .prompt-opt/rendered_cases.jsonl --out .prompt-opt/target_outputs.jsonl --model "$DSPY_MODEL"
```

Package materials for Codex Judge:

```bash
codex-prompt-opt judge-pack .prompt-opt/rendered_cases.jsonl .prompt-opt/target_outputs.jsonl examples/task.json --out .prompt-opt/judge_pack.json
```

After Codex writes `judgement.json`, ingest it and propose the next prompt:

```bash
codex-prompt-opt ingest-judgement .prompt-opt/judgement.json --workdir .prompt-opt
codex-prompt-opt propose examples/prompt.md .prompt-opt/judgement.json --out .prompt-opt/prompts/candidate_002.md --workdir .prompt-opt
```

Run one automated target-model step and produce a judge pack:

```bash
codex-prompt-opt optimize-step examples/prompt.md examples/task.json --workdir .prompt-opt --candidate-id initial --model "$DSPY_MODEL"
```

Finalize the best judged prompt:

```bash
codex-prompt-opt finalize --workdir .prompt-opt --out-dir .prompt-opt/final
```

## Codex Skill

The Codex Skill lives in `skills/codex-prompt-optimizer`. Use it when Codex should orchestrate this workflow, judge target model outputs, and write the structured `judgement.json` consumed by the CLI.

## Directory Structure

```text
src/codex_prompt_optimizer/     CLI implementation
tests/                          Unit tests
examples/                       Minimal prompt and JSON case examples
examples/prompt_jxb_v*.md       JXB prompt iteration history
skills/codex-prompt-optimizer/  Codex Skill for this workflow
PLAN.md                         Original design plan
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

For real model execution, configure `DSPY_MODEL` and credentials first.
