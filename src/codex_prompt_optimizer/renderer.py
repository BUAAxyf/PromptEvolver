from __future__ import annotations

import re
from html import escape
from typing import Any

_DOUBLE_TAG_RE = re.compile(r"{{\s*([#\^\/!>&=]?)\s*([^{}\n]+?)\s*}}")
_TRIPLE_TAG_RE = re.compile(r"{{{\s*([^{}\n]+?)\s*}}}")
_SECTION_RE = re.compile(
    r"{{\s*([#\^])\s*([A-Za-z0-9_.-]+)\s*}}(.*?){{\s*/\s*\2\s*}}",
    re.DOTALL,
)
_MISSING = object()


def extract_mustache_variables(template: str) -> set[str]:
    variables: set[str] = set()
    for match in _TRIPLE_TAG_RE.finditer(template):
        name = _clean_tag_name(match.group(1))
        if name and name != ".":
            variables.add(name)
    for match in _DOUBLE_TAG_RE.finditer(template):
        prefix = match.group(1)
        if prefix in {"/", "!", ">", "="}:
            continue
        name = _clean_tag_name(match.group(2))
        if name and name != ".":
            variables.add(name)
    return variables


def missing_variables(required: set[str], variables: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for name in sorted(required):
        if _resolve(variables, name) is _MISSING:
            missing.append(name)
    return missing


def render_template(template: str, variables: dict[str, Any]) -> str:
    try:
        import chevron  # type: ignore
    except ImportError:
        return _fallback_render(template, variables)
    return chevron.render(template, variables)


def _clean_tag_name(name: str) -> str:
    return name.strip().split()[0].strip("{}&")


def _resolve(context: Any, name: str) -> Any:
    if name == ".":
        return context
    value = context
    for part in name.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return _MISSING
    return value


def _stringify(value: Any) -> str:
    if value is None or value is _MISSING:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _merge_context(parent: dict[str, Any], item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return {**parent, **item}
    return {**parent, ".": item}


def _fallback_render(template: str, variables: dict[str, Any]) -> str:
    rendered = template
    previous = None
    while previous != rendered:
        previous = rendered
        rendered = _SECTION_RE.sub(lambda match: _render_section(match, variables), rendered)

    rendered = _TRIPLE_TAG_RE.sub(
        lambda match: _stringify(_resolve(variables, _clean_tag_name(match.group(1)))),
        rendered,
    )

    def replace_double(match: re.Match[str]) -> str:
        prefix = match.group(1)
        if prefix in {"!", ">", "="}:
            return ""
        if prefix in {"#", "^", "/"}:
            return match.group(0)
        value = _resolve(variables, _clean_tag_name(match.group(2)))
        return escape(_stringify(value))

    return _DOUBLE_TAG_RE.sub(replace_double, rendered)


def _render_section(match: re.Match[str], variables: dict[str, Any]) -> str:
    mode = match.group(1)
    name = match.group(2)
    body = match.group(3)
    value = _resolve(variables, name)
    truthy = bool(value) and value is not _MISSING
    if mode == "^":
        return _fallback_render(body, variables) if not truthy else ""
    if isinstance(value, list):
        return "".join(_fallback_render(body, _merge_context(variables, item)) for item in value)
    if isinstance(value, dict):
        return _fallback_render(body, {**variables, **value})
    return _fallback_render(body, variables) if truthy else ""

