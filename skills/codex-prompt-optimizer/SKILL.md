---
name: codex-prompt-optimizer
description: Use when Codex needs to optimize a Mustache prompt template with a JSON multi-case variables file by orchestrating the local `codex-prompt-opt` CLI, judging target model outputs, writing structured `judgement.json`, ingesting scores, proposing the next prompt template, and finalizing the best prompt without using Codex as the target model executor.
---

# Codex Prompt Optimizer

## Role Split

- Treat the CLI as the target-model runner and prompt-template optimizer.
- Treat Codex as the workflow orchestrator, Judge, and failure analyst.
- Do not use Codex as the target model executor.
- Do not edit the variables JSON unless the user explicitly asks for dataset maintenance. If cases or rubrics are inadequate, report that separately.
- Optimize only the prompt template.

## Required Inputs

- A Mustache prompt template, usually `prompt.md`.
- A JSON variables file with multiple cases, usually `task.json`.
- Optional target thresholds: `target_pass_rate`, `target_average_score_100`, and max iteration budget.

Use these terms consistently:

- `rendered prompt`
- `prompt instantiation`
- `task instance`
- `evaluation case/example`

They all refer to the full task produced by rendering the prompt template with one case's variables.

## Workflow

1. Read the prompt template and variables JSON to understand task intent, expected outputs, and rubric.
2. Check target model configuration before calling the target model:

   ```bash
   codex-prompt-opt config show
   ```

   If required or recommended values are missing, guide the user to configure them with:

   ```bash
   codex-prompt-opt config init
   codex-prompt-opt config set DSPY_MODEL <model-name>
   codex-prompt-opt config set DSPY_API_BASE <api-base-url>
   codex-prompt-opt config set DSPY_API_KEY <api-key>
   codex-prompt-opt config set DSPY__TEMPERATURE 0.1
   codex-prompt-opt config set DSPY__MAX_TOKENS 2048
   codex-prompt-opt config set DSPY__TIMEOUT_SECONDS 90
   codex-prompt-opt config set EVO_EVAL_ENABLE_THINKING true
   ```

   Do not print real API keys in responses or logs.
3. Run validation:

   ```bash
   codex-prompt-opt validate <prompt.md> <task.json>
   ```

4. Run one target-model optimization step:

   ```bash
   codex-prompt-opt optimize-step <prompt.md> <task.json> --workdir .prompt-opt --candidate-id initial --model "$DSPY_MODEL"
   ```

5. Open the generated `judge_pack_<candidate_id>.json`.
6. Judge every case using the task description, case expected value, case rubric, rendered prompt, and target output.
7. Write `.prompt-opt/judgement.json` with the exact structure below.
8. Ingest the judgement:

   ```bash
   codex-prompt-opt ingest-judgement .prompt-opt/judgement.json --workdir .prompt-opt
   ```

9. If thresholds are not met and budget remains, propose a next prompt:

   ```bash
   codex-prompt-opt propose .prompt-opt/prompts/<candidate_id>.md .prompt-opt/judgement.json --out .prompt-opt/prompts/<next_candidate_id>.md --workdir .prompt-opt --candidate-id <next_candidate_id> --parent-candidate-id <candidate_id>
   ```

10. Run `optimize-step` again with the new prompt and repeat.
11. Finalize when thresholds are met or the budget is exhausted:

    ```bash
    codex-prompt-opt finalize --workdir .prompt-opt --out-dir .prompt-opt/final
    ```

## Judgement JSON Contract

Write one judgement object per candidate:

```json
{
  "schema_version": "1.0",
  "candidate_id": "initial",
  "judge": "codex",
  "case_judgements": [
    {
      "case_id": "case id from judge pack",
      "binary_score": 0,
      "score_100": 0,
      "rationale": "Why this score is correct.",
      "failure_tags": ["format_error", "missing_field"],
      "improvement_advice": "Concrete prompt-template change advice."
    }
  ],
  "overall": {
    "summary": "Brief cross-case assessment.",
    "meets_success_criteria": false
  }
}
```

Rules:

- `binary_score` must be `0` or `1`.
- `score_100` must be an integer from `0` to `100`.
- Use `binary_score=1` only when the target output satisfies the essential task requirements for that case.
- Use `score_100` for quality gradient even when `binary_score` is `0`.
- Keep `failure_tags` short and reusable, for example `format_error`, `missing_field`, `wrong_label`, `hallucination`, `unsupported_claim`, `incomplete_reasoning`, `constraint_violation`.
- Make `improvement_advice` actionable for prompt-template rewriting.

## Judge Criteria

Judge against the case-specific `expected` and `rubric` first, then the task-level rubric. If no explicit expected value exists, judge against the rendered prompt instructions and task intent.

Check:

- Correctness of the substantive answer.
- Required output format and fields.
- Constraint following.
- Grounding in provided variables.
- Absence of hallucinated or unsupported details.
- Completeness and usefulness for the task.

## Iteration Policy

- Continue until both target pass rate and target average `score_100` are reached, or the user-defined iteration/budget limit is exhausted.
- Prefer one clear prompt-template change per iteration when judging failure patterns is ambiguous.
- If failures point to missing or contradictory cases/rubrics, stop and report the data issue instead of silently changing the variables file.
- At the end, summarize best candidate metrics, unresolved failure modes, final artifact paths, and whether thresholds were reached.
