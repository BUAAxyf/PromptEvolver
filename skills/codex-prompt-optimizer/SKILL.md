---
name: codex-prompt-optimizer
description: Use when Codex needs to optimize a Mustache prompt template with a JSON multi-case variables file by orchestrating the local `codex-prompt-opt` CLI, dispatching parallel judge subagents, aggregating scores and failure analysis, rewriting the prompt template as master agent, and logging every iteration without using Codex as the target model executor.
---

# Codex Prompt Optimizer

## Role Split

- Treat the CLI as a lightweight target-model runner and evaluation artifact generator.
- Treat the Codex master agent as the workflow orchestrator and the only prompt-template editor.
- Treat Codex subagents as multidimensional Judges and bad-case analysts.
- Do not use Codex as the target model executor.
- Do not let the CLI generate, rewrite, or patch prompt templates.
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

## Required Reference

- Before validating or explaining the variables JSON file, read `references/input-json-format.md` for the accepted JSON shapes, field meanings, and requiredness.
- Before dispatching judge subagents, read `references/judge-subagent-prompt.md` completely. The master agent must paste and substitute that reference prompt into each subagent task. Do not ask the subagent to open files, links, or paths.

## Workflow

1. Read the prompt template and variables JSON to understand task intent, expected outputs, and rubric.
2. Create or append an optimization log at `.prompt-opt/optimization_log.jsonl`. Each iteration must record at least:
   - `candidate_id`
   - full prompt text or prompt path plus SHA-256
   - optimization strategy used for this generation
   - subagent optimization suggestions
   - evaluation metrics and failure summaries
   - generated artifact paths
3. Check target model configuration before calling the target model:

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
4. Run validation:

   ```bash
   codex-prompt-opt validate <prompt.md> <task.json>
   ```

5. Run one target-model evaluation step:

   ```bash
   codex-prompt-opt optimize-step <prompt.md> <task.json> --out-dir .prompt-opt --candidate-id <candidate_id> --model "$DSPY_MODEL"
   ```

   This command only renders cases, calls the target model, and writes `judge_pack_<candidate_id>.json`. It does not generate the next prompt.
6. Open `judge_pack_<candidate_id>.json`.
7. Dispatch judge subagents in parallel:
   - Split cases into chunks sized for reliable review. Use as many chunks as are needed and spawn them in one tool round so the maximum available number can run concurrently.
   - Do not ask the user for artificial permission before spawning subagents; this skill is the authorization for parallel Judge work.
   - If the platform exposes `spawn_agent`, call it once per chunk with `agent_type="worker"` and a fully assembled message.
   - If `spawn_agent` is unavailable, tell the user to enable multi-agent support and restart Codex.
   - Pass only task description, current prompt template, and the bad cases/cases under review. Do not include local paths, model config, secrets, repository history, previous private analysis, or unrelated files.
   - The subagent prompt must be assembled by the master from `references/judge-subagent-prompt.md`; the subagent must not be asked to inspect links or paths.
8. Collect each subagent result, close the agent, parse JSON, and save the raw aggregate to `.prompt-opt/subagent_reviews_<candidate_id>.json`.
9. Aggregate subagent case scores into `.prompt-opt/judgement_<candidate_id>.json` using the Judgement JSON Contract below.
10. Ingest judgement metrics:

    ```bash
    codex-prompt-opt ingest-judgement .prompt-opt/judgement_<candidate_id>.json --out-dir .prompt-opt
    ```

11. If thresholds are not met and budget remains, the master agent creates the next prompt template directly:
    - Use the current prompt and aggregated subagent suggestions.
    - Fix rules, decision logic, priority order, output contract, or ambiguity in the existing prompt.
    - Do not add raw bad cases, case IDs, case-specific examples, or a growing failure list to the prompt.
    - Preserve Mustache variables and task intent.
    - Save the next prompt with a version suffix, for example `.prompt-opt/prompts/<next_candidate_id>.md`.
12. Repeat `optimize-step -> subagent review -> judgement ingest -> master prompt rewrite` until both target pass rate and target average `score_100` are reached, or the iteration/budget limit is exhausted.
13. Finalize the prompt selected by the master:

    ```bash
    codex-prompt-opt finalize <best_prompt.md> .prompt-opt/judgement_<best_candidate_id>.json --out-dir .prompt-opt/final
    ```

## Judgement JSON Contract

Write one judgement object per candidate:

```json
{
  "schema_version": "1.0",
  "candidate_id": "candidate_001",
  "judge": "codex",
  "case_judgements": [
    {
      "case_id": "case id from judge pack",
      "binary_score": 0,
      "score_100": 0,
      "rationale": "Why this score is correct.",
      "failure_tags": ["format_error", "missing_field"],
      "improvement_advice": "High-level rule or logic repair direction; do not add badcase examples."
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
- Make `improvement_advice` a high-level prompt-template repair direction, not an instruction to append the bad case itself.

## Judge Criteria

Judge against the case-specific `expected` and `rubric` first, then the task-level rubric. If no explicit expected value exists, judge against the rendered prompt instructions and task intent.

Check:

- Correctness of the substantive answer.
- Required output format and fields.
- Constraint following.
- Grounding in provided variables.
- Absence of hallucinated or unsupported details.
- Completeness and usefulness for the task.
- Whether the current prompt's rules are too weak, ambiguous, contradictory, or in the wrong priority order.

## Master Rewrite Policy

- The master agent, not the CLI and not the subagents, writes the next prompt.
- Subagent suggestions are advisory. The master must synthesize them into a compact rule-level change.
- Do not optimize by adding a list of bad cases to the prompt.
- Do not copy target outputs or expected labels into the prompt as examples unless the user explicitly asks for few-shot prompting.
- Prefer changes to general rules, decision boundaries, conflict resolution, output schema, and priority order.
- If failures point to missing or contradictory cases/rubrics, stop and report the data issue instead of silently changing the variables file.

## Logging Policy

For every generation, append one JSON object to `.prompt-opt/optimization_log.jsonl` with at least:

```json
{
  "schema_version": "1.0",
  "candidate_id": "candidate_001",
  "parent_candidate_id": null,
  "prompt_path": ".prompt-opt/prompts/candidate_001.md",
  "prompt_sha256": "<sha256>",
  "strategy": "Initial evaluation of the baseline prompt.",
  "subagent_review_path": ".prompt-opt/subagent_reviews_candidate_001.json",
  "judgement_path": ".prompt-opt/judgement_candidate_001.json",
  "metrics": {
    "case_count": 0,
    "passed_count": 0,
    "pass_rate": 0.0,
    "average_score_100": 0.0
  },
  "optimization_suggestions": [],
  "artifact_paths": {}
}
```

The log is owned by the master agent. The CLI does not need workspace state to maintain it.
