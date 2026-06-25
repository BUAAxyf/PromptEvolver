---
name: prompt-evolver
description: Use when optimizing a Mustache prompt template with JSON cases through the local `prompt-evolver` CLI, including default stratified train/hidden-eval splitting, train-set bad-case analysis, black-box hidden evaluation scoring, parallel judge review, prompt rewriting, and iteration logging.
---

# Prompt Evolver

## Role Split

- Treat the CLI as a lightweight target-model runner and evaluation artifact generator.
- Treat the Codex master agent as the workflow orchestrator and the only prompt-template editor.
- Treat Codex subagents as multidimensional Judges and bad-case analysts.
- Do not use Codex as the target model executor.
- Do not let the CLI generate, rewrite, or patch prompt templates.
- Do not edit the variables JSON unless the user explicitly asks for dataset maintenance. If cases or rubrics are inadequate, report that separately.
- Optimize only the prompt template.
- Keep bad-case analysis and hidden evaluation isolated. The training set may be opened for bad-case analysis; the hidden evaluation set must not be opened and may only feed `blackbox-eval`, which returns aggregate scores.

## Required Inputs

- A Mustache prompt template, usually `prompt.md`.
- A JSON variables file with multiple cases, usually `task.json`.
- Optional target thresholds: `target_pass_rate`, `target_average_score_100`, and max iteration budget.
- Optional explicit train/test files. If the user does not specify whether to split data, split the provided variables file with the default stratified 70% train / 30% test method.

Use these terms consistently:

- `rendered prompt`
- `prompt instantiation`
- `task instance`
- `evaluation case/example`

They all refer to the full task produced by rendering the prompt template with one case's variables.

## Required Reference

- Before validating or explaining the variables JSON file, read `references/input-json-format.md` for the accepted JSON shapes, field meanings, and requiredness.
- Before dispatching judge subagents, read `references/judge-subagent-prompt.md` completely. The master agent must paste and substitute that reference prompt into each subagent task. Do not ask the subagent to open files, links, or paths.

## Input JSON Validation Script

Before running target-model evaluation, validate the variables JSON with the bundled deterministic script:

```bash
python skills/prompt-evolver/scripts/validate_input_json.py <task.json> --prompt <prompt.md>
```

The script checks the root shape, case-list field, field types, unique case IDs, `globals` merging, inferred variables, and optional Mustache variable coverage. It prints a JSON report and exits non-zero when the input does not match `references/input-json-format.md`.

## Train/Hidden Evaluation Policy

- If only one variables file is provided and the user does not explicitly opt out of splitting, run:

  ```bash
  prompt-evolver split <task.json> --train-out .prompt-evolver/train.json --test-out .prompt-evolver/test.json
  ```

- The split command uses deterministic stratified sampling by `expected.ground_truth`, with default `--train-ratio 0.7`.
- Treat the training output as `train_json` and the test output as `eval_json`.
- During training, all bad-case generation, judge packs, subagent review, and failure analysis must point to `train_json` only.
- Do not open, inspect, summarize, copy, or show hidden evaluation cases, expected labels, rubrics, target outputs, judge prompts, per-case scores, or ground-truth distributions.
- The master agent may use only aggregate `blackbox-eval` metrics from `eval_json`, such as `pass_rate`, `scored_count`, and parse/invalid counts.
- Because the hidden evaluation score guides prompt selection each iteration, it is a black-box optimization signal rather than a pristine final held-out test.
- Do not dispatch judge subagents on `eval_json`, do not build judge packs from `eval_json`, and do not rewrite the prompt from any hidden case-level information.

## Subagent Context Isolation Policy

The master must treat every Judge subagent as context-isolated. Do not rely on inherited parent conversation, prior tool output, local files, or repository paths.

Previous failure mode to avoid: the master spawned subagents with messages such as "use the prompt shown in the parent context" while also instructing them not to read files. The subagents could not see the referenced prompt and correctly returned "missing context" instead of judging. Prevent this by making each spawned message fully self-contained.

Required rules:

- Do not set `fork_context=true` when spawning Judge subagents.
- Do not ask a subagent to use parent-thread context, earlier messages, local paths, files, links, logs, or previously displayed terminal output.
- Do not write messages that refer to "the above prompt", "the parent context", "the file at ...", "the judge pack path", or "the prompt I just printed".
- Paste the fully assembled Judge prompt text directly into each `spawn_agent` message.
- Substitute every placeholder from `references/judge-subagent-prompt.md` before spawning, including task description, current prompt template, case JSON, and subagent id.
- Include the exact JSON output contract in the spawned message.
- Save subagent prompts to disk for audit if useful, but still paste the prompt content into the spawn message; saved files are not a substitute for subagent input.
- If the assembled message is too large, reduce the case chunk size or compact case fields. Never replace required content with a path or a parent-context reference.
- If a subagent reports missing context, close it, rebuild a self-contained message, and respawn without inherited context.

## Workflow

1. Read the prompt template and variables JSON only as needed to identify the input shape and prepare datasets. Do not inspect hidden evaluation case content, expected outputs, labels, or distributions.
2. Prepare datasets:
   - If explicit train/test files are provided, use them as given.
   - Otherwise run the default split command from the Train/Test Phase Policy.
   - Set `train_json` to the training file and `eval_json` to the hidden evaluation file.
   - Validate `eval_json` format only when needed; do not inspect its case content, labels, distributions, outputs, or expected values.
   - Use `train_json` to understand task intent, expected outputs, and rubric for prompt iteration.
3. Create or append an optimization log at `.prompt-evolver/optimization_log.jsonl`. Each iteration must record at least:
   - `candidate_id`
   - full prompt text or prompt path plus SHA-256
   - optimization strategy used for this generation
   - subagent optimization suggestions
   - training-set evaluation metrics and failure summaries
   - hidden evaluation aggregate score path and metrics
   - generated artifact paths
4. Check target and evaluator model configuration before calling models:

   ```bash
   prompt-evolver config show
   ```

   If required or recommended values are missing, guide the user to configure them with:

   ```bash
   prompt-evolver config init
   prompt-evolver config set MODEL_NAME <model-name>
   prompt-evolver config set MODEL_API_BASE <api-base-url>
   prompt-evolver config set MODEL_API_KEY <api-key>
   prompt-evolver config set MODEL_TEMPERATURE 0.1
   prompt-evolver config set MODEL_MAX_TOKENS 2048
   prompt-evolver config set MODEL_TIMEOUT_SECONDS 90
   prompt-evolver config set MODEL_ENABLE_THINKING true
   ```

   `blackbox-eval` uses `EVALUATOR_MODEL_*` for the independent evaluator. Leave `EVALUATOR_MODEL_*` blank to reuse the corresponding `MODEL_*` values. Set them only when the evaluator should differ:

   ```bash
   prompt-evolver config set EVALUATOR_MODEL_NAME <model-name>
   prompt-evolver config set EVALUATOR_MODEL_API_BASE <api-base-url>
   prompt-evolver config set EVALUATOR_MODEL_API_KEY <api-key>
   ```

   Do not print real API keys in responses or logs.
5. Run training-set validation:

   ```bash
   prompt-evolver validate <prompt.md> <train_json>
   ```

6. Run one training-set target-model evaluation step:

   ```bash
   prompt-evolver optimize-step <prompt.md> <train_json> --out-dir .prompt-evolver --candidate-id <candidate_id> --model "$MODEL_NAME"
   ```

   This command only renders cases, calls the target model, and writes `judge_pack_<candidate_id>.json`. It does not generate the next prompt.
7. Open `judge_pack_<candidate_id>.json`.
8. Dispatch judge subagents in parallel:
   - Split cases into chunks sized for reliable review. Use as many chunks as are needed and spawn them in one tool round so the maximum available number can run concurrently.
   - Do not ask the user for artificial permission before spawning subagents; this skill is the authorization for parallel Judge work.
   - If the platform exposes `spawn_agent`, call it once per chunk with `agent_type="worker"` and a fully assembled, self-contained message. Leave `fork_context` unset or set it to `false`; do not inherit parent context.
   - If `spawn_agent` is unavailable, tell the user to enable multi-agent support and restart Codex.
   - Pass only task description, current prompt template, and the bad cases/cases under review. Do not include local paths, model config, secrets, repository history, previous private analysis, or unrelated files.
   - The subagent prompt must be assembled by the master from `references/judge-subagent-prompt.md`; the subagent must not be asked to inspect links, paths, parent context, or prior messages.
   - Before spawning, check the message text for unresolved placeholders (`<<...>>`) and forbidden context references such as `parent context`, `父线程`, `上文`, `path`, `file`, or local filesystem paths. Fix them before dispatch.
9. Collect each subagent result, close the agent, parse JSON, and save the raw aggregate to `.prompt-evolver/subagent_reviews_<candidate_id>.json`.
10. Aggregate subagent case scores into `.prompt-evolver/judgement_<candidate_id>.json` using the Judgement JSON Contract below.
11. Ingest judgement metrics:

    ```bash
    prompt-evolver ingest-judgement .prompt-evolver/judgement_<candidate_id>.json --out-dir .prompt-evolver
    ```

12. Run hidden black-box evaluation for the same candidate:

    ```bash
    prompt-evolver blackbox-eval <prompt.md> <eval_json> --out-dir .prompt-evolver --candidate-id <candidate_id>
    ```

    Required handling:
    - Read only the aggregate result from `.prompt-evolver/blackbox_score_<candidate_id>.json`.
    - Use `pass_rate` as the hidden evaluation objective-function signal for prompt selection.
    - Do not open `eval_json`, rendered hidden prompts, target outputs, judge prompts, case IDs, per-case scores, or evaluator rationales.
    - The command must not be replaced with `test-step`, `render`, `run`, `judge-pack`, or subagent review on `eval_json`.
13. If thresholds are not met and budget remains, the master agent creates the next prompt template directly:
    - Use the current prompt and aggregated subagent suggestions.
    - Use the hidden aggregate score only as a black-box objective signal, for example to prefer candidates with higher hidden `pass_rate` and reject train-only improvements that degrade it.
    - Fix rules, decision logic, priority order, output contract, or ambiguity in the existing prompt.
    - Do not add raw bad cases, case IDs, case-specific examples, or a growing failure list to the prompt.
    - Preserve Mustache variables and task intent.
    - Save the next prompt with a version suffix, for example `.prompt-evolver/prompts/<next_candidate_id>.md`.
14. Repeat `optimize-step -> subagent review -> judgement ingest -> blackbox-eval -> master prompt rewrite` until the training thresholds and hidden evaluation objective are satisfactory, or the iteration/budget limit is exhausted.
15. Finalize the prompt selected by the combined training review and hidden aggregate score:

    ```bash
    prompt-evolver finalize <best_prompt.md> .prompt-evolver/judgement_<best_candidate_id>.json --out-dir .prompt-evolver/final
    ```
16. If the user explicitly asks for a final file-based accuracy audit, run `test-step` only after optimization stops:

    ```bash
    prompt-evolver test-step <best_prompt.md> <eval_json> --out-dir .prompt-evolver --candidate-id final_test --model "$MODEL_NAME"
    ```

    `test-step` writes rendered prompts and target outputs, so do not use it during black-box optimization. Do not rewrite the prompt after this optional audit.
17. Open the prompt diff review UI for the user:

    ```bash
    prompt-evolver prompt-diff <input_prompt.md> .prompt-evolver/final/best_prompt.md
    ```

    The command starts a local foreground server, opens the browser when possible, and prints the review URL. Tell the user to open the URL and inspect the side-by-side prompt diff; stop the server with `Ctrl+C` after review.

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

For every generation, append one JSON object to `.prompt-evolver/optimization_log.jsonl` with at least:

```json
{
  "schema_version": "1.0",
  "candidate_id": "candidate_001",
  "parent_candidate_id": null,
  "prompt_path": ".prompt-evolver/prompts/candidate_001.md",
  "prompt_sha256": "<sha256>",
  "strategy": "Initial evaluation of the baseline prompt.",
  "subagent_review_path": ".prompt-evolver/subagent_reviews_candidate_001.json",
  "judgement_path": ".prompt-evolver/judgement_candidate_001.json",
  "metrics": {
    "case_count": 0,
    "passed_count": 0,
    "pass_rate": 0.0,
    "average_score_100": 0.0
  },
  "hidden_eval": {
    "score_report": ".prompt-evolver/blackbox_score_candidate_001.json",
    "pass_rate": 0.0,
    "scored_count": 0
  },
  "optimization_suggestions": [],
  "artifact_paths": {}
}
```

The log is owned by the master agent. The CLI does not need workspace state to maintain it.
