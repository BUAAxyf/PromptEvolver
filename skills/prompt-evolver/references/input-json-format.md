# Input Variables JSON Format

This reference defines the variables JSON file accepted by `prompt-evolver`. The file contains multiple evaluation cases used to render one Mustache prompt template into many task instances.

## Root Format

The CLI accepts either a root object or a root array.

### Root Object

```json
{
  "task": {
    "name": "Support classifier",
    "description": "Classify support requests.",
    "rubric": "The predicted label must match expected.label."
  },
  "globals": {
    "locale": "zh-CN"
  },
  "cases": [
    {
      "id": "refund_001",
      "variables": {
        "request": "I was charged twice and need a refund."
      },
      "expected": {
        "label": "billing"
      },
      "rubric": "Label must be billing.",
      "metadata": {
        "source": "manual",
        "label_status": "adjudicated",
        "split_group": "refund_semantic_group"
      }
    }
  ]
}
```

### Root Array Shorthand

```json
[
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
```

When the root is an array, it is treated as the case list. `task` and `globals` default to empty objects.

## Root Fields

| Field | Type | Required | Meaning |
|---|---|---:|---|
| `task` | object | No | Task-level metadata for structured review, such as `name`, `description`, and `rubric`. The CLI passes it into the judge pack but does not enforce inner field names. |
| `globals` | object | No | Variables merged into every case before rendering. Case-level variables override duplicate global keys. |
| `cases` | array<object> | Conditionally yes | Primary case list field. Required when the root is an object unless `examples` or `evaluation_cases` is provided. Must contain at least one item. |
| `examples` | array<object> | Conditionally yes | Alias for `cases`. Use only one case-list field for clarity. |
| `evaluation_cases` | array<object> | Conditionally yes | Alias for `cases`. Use only one case-list field for clarity. |

Root object validation rules:

- At least one of `cases`, `examples`, or `evaluation_cases` must exist.
- The selected case-list field must be a non-empty array.
- `task`, when present, must be an object.
- `globals`, when present, must be an object.

## Case Fields

| Field | Type | Required | Meaning |
|---|---|---:|---|
| `id` | string-compatible | No | Preferred case identifier. Must be unique after string conversion. |
| `case_id` | string-compatible | No | Alias for `id`. Used only when `id` is absent. |
| `variables` | object | No | Variables used to render the Mustache prompt template. Required in practice when all variables are not expressed as top-level non-reserved fields. |
| `expected` | any JSON value | No | Expected answer or labels for structured review and accuracy scoring. Passed through unchanged. |
| `expected_output` | any JSON value | No | Alias for `expected`; used only when `expected` is absent. |
| `rubric` | any JSON value | No | Case-specific judging guidance. Passed through unchanged. |
| `metadata` | object | No | Extra case metadata. Formal three-way evaluation requires `label_status="adjudicated"` and a non-empty `split_group`. |
| `notes` | any JSON value | No | Reserved case-level notes. The CLI does not render this field into variables unless it is repeated inside `variables`. |

Case validation and normalization rules:

- Each case must be an object.
- If both `id` and `case_id` are absent, the CLI assigns `case_001`, `case_002`, etc.
- Case IDs must be unique.
- If `variables` is present, it must be an object.
- If `variables` is absent, every top-level case field except reserved case fields becomes a render variable.
- Reserved case fields are `id`, `case_id`, `variables`, `expected`, `expected_output`, `rubric`, `metadata`, and `notes`.
- Effective render variables are `globals` merged with case variables; case variables win on key conflict.
- The CLI never modifies the variables JSON during optimization.

## Train/Development/Final-Test Governance And Accuracy Fields

Formal optimization uses three explicit datasets. Before strict initialization, audit each file:

```bash
prompt-evolver data-audit <train.json>
prompt-evolver data-audit <dev.json>
prompt-evolver data-audit <test.json>
```

Every formal-evaluation case must contain:

```json
{
  "metadata": {
    "label_status": "adjudicated",
    "split_group": "stable_semantic_group"
  }
}
```

The audit reports conflicting exact duplicates, singleton label pairs, missing split groups, and unadjudicated cases. `strict init` rejects invalid governed files and any `split_group` that crosses train, development, and final-test datasets.

Initialize the formal workflow with:

```bash
prompt-evolver strict init <source.json> --prompt <prompt.md> --out-dir .prompt-evolver --train-json <train.json> --dev-json <dev.json> --test-json <test.json>
```

The legacy split remains available for local experiments:

When the workflow needs train/test files and the user has not provided them, run:

```bash
prompt-evolver split <task.json> --train-out .prompt-evolver/train.json --test-out .prompt-evolver/test.json
```

The split command preserves the root shape and top-level metadata, then writes two variables JSON files. Treat the first output as training data and the second as development data; it is not an untouched final test. The command uses deterministic stratified sampling with default `--train-ratio 0.7` and `--seed 13`. The stratification key is selected in this order:

1. `expected.ground_truth`
2. `expected.primary`
3. The complete `expected` value

For development/final evaluation and accuracy scoring, prefer this `expected` shape when possible:

```json
{
  "expected": {
    "ground_truth": {
      "label": "billing"
    },
    "acceptable_outputs": [
      {
        "label": "billing"
      }
    ]
  }
}
```

The strict exact scorer, `prompt-evolver score-accuracy`, and `prompt-evolver test-step` score each target output with this priority:

- `expected.acceptable_outputs`, when it is a non-empty array.
- `expected.primary`, when present.
- `expected.ground_truth`, when present.
- The complete `expected` value as a fallback.

For object expected values, the expected object only needs to be a subset of the target output object, so extra model fields are allowed. `ground_truth` may also be a string containing JSON alternatives separated by ` or `, for example `{"label":"billing"} or {"label":"refund"}`.

Training bad-case analysis must use only the training JSON. Development data may feed `strict dev-score-candidate`, but only aggregate metrics may guide candidate selection. Final-test data may feed `strict final-eval` once after selection. Development and final-test case content, expected answers, rendered prompts, target outputs, judge prompts, and per-case scores must not be opened or used for prompt rewriting.

## Prompt Variable Requirements

The prompt template uses Mustache placeholders such as `{{request}}` or `{{user.name}}`. For every case, all variables required by the template must exist in that case's effective render variables after `globals` are merged.

Run deterministic format validation first:

```bash
python skills/prompt-evolver/scripts/validate_input_json.py <task.json> --prompt <prompt.md>
```

Run validation before model calls:

```bash
prompt-evolver validate <prompt.md> <task.json>
```

If validation reports `missing_variables_by_case`, fix the JSON input or prompt template manually. The workflow must not silently rewrite the variables file.

## Minimal Valid Examples

Variables nested under `variables`:

```json
{
  "cases": [
    {
      "id": "case_a",
      "variables": {
        "request": "refund please"
      }
    }
  ]
}
```

Variables inferred from top-level non-reserved fields:

```json
{
  "cases": [
    {
      "id": "case_a",
      "request": "refund please",
      "expected": {
        "label": "billing"
      }
    }
  ]
}
```
