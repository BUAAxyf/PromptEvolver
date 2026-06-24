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
        "source": "manual"
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
| `task` | object | No | Task-level metadata for Codex Judge, such as `name`, `description`, and `rubric`. The CLI passes it into the judge pack but does not enforce inner field names. |
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
| `expected` | any JSON value | No | Expected answer or labels for Codex Judge. Passed through unchanged. |
| `expected_output` | any JSON value | No | Alias for `expected`; used only when `expected` is absent. |
| `rubric` | any JSON value | No | Case-specific judging guidance. Passed through unchanged. |
| `metadata` | object | No | Extra case metadata for judge analysis or reporting. Must be an object when present. |
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
