---
name: prompt-evolver
description: Use when optimizing a Mustache prompt template with JSON cases through the local `prompt-evolver` CLI, including governed train/dev/test datasets, train-set bad-case analysis, aggregate development scoring, one-time final evaluation, parallel judge review, prompt rewriting, and iteration logging.
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
- Keep training, development, and final-test roles isolated. Only training cases may be opened for bad-case analysis; development and final-test execution may persist aggregate scores only.

## Required Inputs

- A Mustache prompt template, usually `prompt.md`.
- A JSON variables file with multiple cases, usually `task.json`.
- Optional target thresholds: `target_pass_rate`, `target_average_score_100`, and max iteration budget.
- For formal optimization, explicit governed train/dev/test files. The legacy 70% / 30% split is only for local experiments and treats its second output as development data.

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

## Train/Development/Final-Test Policy

- Formal optimization requires explicit train, development, and final-test files.
- Before initialization, run `prompt-evolver data-audit` on all three files. Every case must have `metadata.label_status="adjudicated"` and a non-empty `metadata.split_group`; conflicting duplicates are forbidden.
- A `split_group` must not appear in more than one dataset.
- All bad-case generation, judge packs, subagent review, and failure analysis must point to `train_json` only.
- Development data may feed `strict dev-score-candidate`; read only aggregate pass rate, JSON validity, and L2/L3 Macro-F1.
- Final-test data may feed `strict final-eval` only after candidate selection. It runs exactly once and locks the trace.
- Do not open, inspect, summarize, copy, or show development/final-test cases, expected labels, rubrics, target outputs, per-case scores, or distributions during prompt optimization.
- If only one variables file is available, the legacy split command may be used for experiments, but its second output is development data and cannot support a final-test claim.

## Strict Mode Enforcement

For formal prompt optimization tasks, use the strict state-machine CLI. Do not count legacy command output as an optimization candidate unless it is recorded through `prompt-evolver strict ...` and `strict verify` passes.

Required strict rules:

- Initialize the trace with `prompt-evolver strict init`.
- For every candidate, run the full strict sequence in order:
  `strict train-candidate -> strict ingest-candidate -> strict dev-score-candidate -> strict log-candidate`.
- Default to `--scorer exact` when expected values are structured. Use `--scorer llm` only when deterministic matching cannot represent the rubric.
- Reviews must be non-empty, cover every failed judgement case, attach evidence IDs to loopholes, and include non-empty optimization suggestions.
- Do not hand-write `optimization_log.jsonl`; let `strict log-candidate` append complete records with training metrics, development metrics, and the actual suggestions.
- After `strict verify`, run `strict final-eval` once for the highest development-score candidate, then run `strict finalize`.
- If any strict command fails, stop and report the missing state or artifact instead of continuing with the next candidate.
- Development and final-test isolation still applies: strict state and logs may include only aggregate metrics, never case IDs, rendered prompts, target outputs, judge prompts, evaluator rationales, or per-case scores.

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

Use the strict CLI for the state transitions below. The lower-level commands shown in this section explain what each strict step wraps, but they are debugging primitives and must not be used to bypass strict state checks during formal optimization.

1. Read the prompt template and training JSON to identify the input shape and task intent. Do not inspect development or final-test case content, labels, or distributions.
2. Prepare datasets:
   - Audit explicit `train_json`, `dev_json`, and `test_json` with `prompt-evolver data-audit`.
   - Require adjudicated labels, non-empty split groups, no conflicting duplicates, and no split-group overlap.
   - Use `train_json` for prompt iteration, `dev_json` for aggregate candidate selection, and `test_json` for one final aggregate evaluation.
3. Create or append an optimization log at `.prompt-evolver/optimization_log.jsonl`. Each iteration must record at least:
   - `candidate_id`
   - full prompt text or prompt path plus SHA-256
   - optimization strategy used for this generation
   - subagent optimization suggestions
   - training-set evaluation metrics and failure summaries
   - development aggregate score path and metrics
   - generated artifact paths
   In strict mode, this file is appended only by `prompt-evolver strict log-candidate`; do not edit it by hand.
4. Check target and evaluator model configuration before calling models:

   ```bash
   prompt-evolver config show
   ```

   If required or recommended values are missing, guide the user to configure them with:

   ```bash
   prompt-evolver config init
   prompt-evolver config set TRAIN_MODEL_NAME <model-name>
   prompt-evolver config set TRAIN_MODEL_API_BASE <api-base-url>
   prompt-evolver config set TRAIN_MODEL_API_KEY <api-key>
   prompt-evolver config set TRAIN_MODEL_TEMPERATURE 0
   prompt-evolver config set TRAIN_MODEL_MAX_TOKENS 2048
   prompt-evolver config set TRAIN_MODEL_TIMEOUT_SECONDS 90
   prompt-evolver config set TRAIN_MODEL_ENABLE_THINKING true
   ```

   The exact scorer does not use `EVALUATOR_MODEL_*`. Configure an independent evaluator only when `--scorer llm` is required:

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

6. Run one strict training-set target-model evaluation step:

   ```bash
   prompt-evolver strict train-candidate <prompt.md> --out-dir .prompt-evolver --candidate-id <candidate_id> --strategy "<single hypothesis>" --model "$TRAIN_MODEL_NAME"
   ```

   This command registers the candidate, renders training cases, calls the target model, and writes `judge_pack_<candidate_id>.json`. It does not generate the next prompt.
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
11. Ingest judgement metrics and the complete subagent review:

    ```bash
    prompt-evolver strict ingest-candidate .prompt-evolver/judgement_<candidate_id>.json --out-dir .prompt-evolver --candidate-id <candidate_id> --subagent-reviews .prompt-evolver/subagent_reviews_<candidate_id>.json
    ```

12. Run aggregate development evaluation for the same candidate:

    ```bash
    prompt-evolver strict dev-score-candidate --out-dir .prompt-evolver --candidate-id <candidate_id> --scorer exact
    prompt-evolver strict log-candidate --out-dir .prompt-evolver --candidate-id <candidate_id>
    ```

    Required handling:
    - Read only aggregate development metrics.
    - Select by pass rate, then L3 Macro-F1, L2 Macro-F1, and shorter prompt length.
    - Do not open development cases or replace aggregate scoring with case-level review.
13. If thresholds are not met and budget remains, the master agent creates the next prompt template directly:
    - Use the current prompt and aggregated subagent suggestions.
    - Use development aggregate metrics to reject train-only improvements that do not generalize.
    - Fix rules, decision logic, priority order, output contract, or ambiguity in the existing prompt.
    - Do not add raw bad cases, case IDs, case-specific examples, or a growing failure list to the prompt.
    - Preserve Mustache variables and task intent.
    - Save the next prompt with a version suffix, for example `.prompt-evolver/prompts/<next_candidate_id>.md`.
14. Generate at most three single-hypothesis variants per round: one boundary repair, one simplification, and one ablation. Do not exceed 12 formal candidates by default.
15. Repeat the strict candidate sequence until development criteria are met or the candidate budget is exhausted.
16. Verify, run the selected candidate on the final test exactly once, and finalize:

    ```bash
    prompt-evolver strict verify --out-dir .prompt-evolver
    prompt-evolver strict final-eval --out-dir .prompt-evolver --candidate-id <best_candidate_id>
    prompt-evolver strict finalize --out-dir .prompt-evolver
    ```
17. Open the prompt diff review UI for the user:

    ```bash
    prompt-evolver prompt-diff <input_prompt.md> .prompt-evolver/final/best_prompt.md
    ```

    The command starts a local foreground server, opens the browser when possible, and prints the review URL. Tell the user to open the URL and inspect the side-by-side prompt diff; stop the server with `Ctrl+C` after review.

## Judgement JSON Contract

Write one judgement object per candidate:

```json
{
  "schema_version": "2.0",
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
  "dev_eval": {
    "score_report": ".prompt-evolver/dev_score_candidate_001.json",
    "pass_rate": 0.0,
    "scored_count": 0,
    "l2_macro_f1": 0.0,
    "l3_macro_f1": 0.0
  },
  "optimization_suggestions": [
    {"type": "boundary_definition", "guidance": "Clarify one reusable decision boundary."}
  ],
  "artifact_paths": {}
}
```

The strict CLI owns the log and appends it only after training review and development scoring are complete.
