#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

CASE_LIST_FIELDS = ("cases", "examples", "evaluation_cases")
RESERVED_CASE_KEYS = {
    "id",
    "case_id",
    "variables",
    "expected",
    "expected_output",
    "rubric",
    "metadata",
    "notes",
}

DOUBLE_TAG_RE = re.compile(r"{{\s*([#\^\/!>&=]?)\s*([^{}\n]+?)\s*}}")
TRIPLE_TAG_RE = re.compile(r"{{{\s*([^{}\n]+?)\s*}}}")
MISSING = object()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate codex-prompt-optimizer input variables JSON.",
    )
    parser.add_argument("input_json", type=Path, help="Variables JSON file to validate.")
    parser.add_argument(
        "--prompt",
        type=Path,
        help="Optional Mustache prompt template; validates every case has required variables.",
    )
    args = parser.parse_args(argv)

    report = validate_file(args.input_json, args.prompt)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 1


def validate_file(input_json: Path, prompt: Path | None = None) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    case_ids: list[str] = []
    normalized_cases: list[dict[str, Any]] = []
    selected_case_field: str | None = None

    data = _load_json(input_json, errors)
    if not errors:
        selected_case_field, normalized_cases, case_ids = _validate_root(data, errors, warnings)

    template_variables: list[str] = []
    missing_variables_by_case: dict[str, list[str]] = {}
    if prompt is not None:
        template = _read_text(prompt, errors)
        if template is not None:
            required = extract_mustache_variables(template)
            template_variables = sorted(required)
            for case in normalized_cases:
                missing = missing_variables(required, case["effective_variables"])
                if missing:
                    missing_variables_by_case[case["case_id"]] = missing
            if missing_variables_by_case:
                errors.append("prompt variables are missing in one or more cases")

    return {
        "schema_version": "1.0",
        "input_json": str(input_json),
        "prompt": str(prompt) if prompt is not None else None,
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "selected_case_field": selected_case_field,
        "case_count": len(normalized_cases),
        "case_ids": case_ids,
        "template_variables": template_variables,
        "missing_variables_by_case": missing_variables_by_case,
    }


def _load_json(path: Path, errors: list[str]) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        errors.append(f"input_json does not exist: {path}")
    except json.JSONDecodeError as exc:
        errors.append(f"input_json is not valid JSON: {exc}")
    except OSError as exc:
        errors.append(f"failed to read input_json: {exc}")
    return None


def _read_text(path: Path, errors: list[str]) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        errors.append(f"prompt does not exist: {path}")
    except OSError as exc:
        errors.append(f"failed to read prompt: {exc}")
    return None


def _validate_root(
    data: Any,
    errors: list[str],
    warnings: list[str],
) -> tuple[str | None, list[dict[str, Any]], list[str]]:
    task: dict[str, Any] = {}
    globals_: dict[str, Any] = {}
    raw_cases: Any
    selected_case_field: str | None

    if isinstance(data, list):
        raw_cases = data
        selected_case_field = "<root-array>"
    elif isinstance(data, dict):
        task_value = data.get("task", {})
        if not isinstance(task_value, dict):
            errors.append("root field 'task' must be an object when provided")
        else:
            task = task_value

        globals_value = data.get("globals", {})
        if not isinstance(globals_value, dict):
            errors.append("root field 'globals' must be an object when provided")
        else:
            globals_ = globals_value

        present_case_fields = [field for field in CASE_LIST_FIELDS if field in data]
        if not present_case_fields:
            errors.append("root object must include 'cases', 'examples', or 'evaluation_cases'")
            return None, [], []
        if len(present_case_fields) > 1:
            warnings.append(
                "multiple case-list fields are present; using first non-empty field "
                "in cases/examples/evaluation_cases order"
            )
        selected_case_field, raw_cases = _select_case_list(data)
    else:
        errors.append("root JSON value must be an object or an array of cases")
        return None, [], []

    if errors:
        return selected_case_field, [], []
    if not isinstance(raw_cases, list):
        errors.append(f"case list '{selected_case_field}' must be an array")
        return selected_case_field, [], []
    if not raw_cases:
        errors.append(f"case list '{selected_case_field}' must contain at least one case")
        return selected_case_field, [], []

    normalized_cases, case_ids = _validate_cases(raw_cases, globals_, errors)
    _ = task
    return selected_case_field, normalized_cases, case_ids


def _select_case_list(data: dict[str, Any]) -> tuple[str, Any]:
    for field in CASE_LIST_FIELDS:
        value = data.get(field)
        if value:
            return field, value
    field = next(field for field in CASE_LIST_FIELDS if field in data)
    return field, data.get(field)


def _validate_cases(
    raw_cases: list[Any],
    globals_: dict[str, Any],
    errors: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    seen_ids: set[str] = set()
    case_ids: list[str] = []
    normalized: list[dict[str, Any]] = []

    for index, raw_case in enumerate(raw_cases, start=1):
        location = f"case #{index}"
        if not isinstance(raw_case, dict):
            errors.append(f"{location} must be an object")
            continue

        case_id = str(raw_case.get("id") or raw_case.get("case_id") or f"case_{index:03d}")
        if case_id in seen_ids:
            errors.append(f"duplicate case id: {case_id}")
        seen_ids.add(case_id)
        case_ids.append(case_id)

        variables_value = raw_case.get("variables")
        if variables_value is None:
            variables = {
                key: value for key, value in raw_case.items() if key not in RESERVED_CASE_KEYS
            }
        elif isinstance(variables_value, dict):
            variables = variables_value
        else:
            errors.append(f"case {case_id}: variables must be an object when provided")
            variables = {}

        metadata_value = raw_case.get("metadata")
        if metadata_value is not None and not isinstance(metadata_value, dict):
            errors.append(f"case {case_id}: metadata must be an object when provided")

        normalized.append(
            {
                "case_id": case_id,
                "effective_variables": {**globals_, **variables},
            }
        )

    return normalized, case_ids


def extract_mustache_variables(template: str) -> set[str]:
    variables: set[str] = set()
    for match in TRIPLE_TAG_RE.finditer(template):
        name = _clean_tag_name(match.group(1))
        if name and name != ".":
            variables.add(name)
    for match in DOUBLE_TAG_RE.finditer(template):
        prefix = match.group(1)
        if prefix in {"/", "!", ">", "="}:
            continue
        name = _clean_tag_name(match.group(2))
        if name and name != ".":
            variables.add(name)
    return variables


def missing_variables(required: set[str], variables: dict[str, Any]) -> list[str]:
    return [
        name
        for name in sorted(required)
        if _resolve_dotted_name(variables, name) is MISSING
    ]


def _clean_tag_name(name: str) -> str:
    return name.strip().split()[0].strip("{}&")


def _resolve_dotted_name(context: Any, name: str) -> Any:
    value = context
    for part in name.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return MISSING
    return value


if __name__ == "__main__":
    raise SystemExit(main())
