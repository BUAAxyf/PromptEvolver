import tempfile
import unittest
from pathlib import Path

from prompt_evolver.renderer import extract_mustache_variables, render_template
from prompt_evolver.workflow import render_cases, validate_inputs


class RendererTests(unittest.TestCase):
    def test_extracts_variables_and_sections(self):
        template = "Hello {{user.name}}{{#items}} {{label}}{{/items}}{{! ignored}}"
        self.assertEqual(extract_mustache_variables(template), {"user.name", "items", "label"})

    def test_render_cases_keeps_variables_file_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prompt = root / "prompt.md"
            variables = root / "task.json"
            rendered = root / "rendered.jsonl"
            prompt.write_text("Request: {{request}}", encoding="utf-8")
            original_json = (
                '{"cases":[{"id":"c1","variables":{"request":"refund please"}}]}\n'
            )
            variables.write_text(original_json, encoding="utf-8")

            validation = validate_inputs(prompt, variables)
            self.assertTrue(validation["valid"])
            records = render_cases(prompt, variables, rendered, "candidate_a")

            self.assertEqual(records[0]["rendered_prompt"], "Request: refund please")
            self.assertEqual(variables.read_text(encoding="utf-8"), original_json)

    def test_fallback_renderer_supports_simple_sections(self):
        rendered = render_template(
            "{{#items}}{{name}}={{value}};{{/items}}",
            {"items": [{"name": "a", "value": 1}, {"name": "b", "value": 2}]},
        )
        self.assertEqual(rendered, "a=1;b=2;")


if __name__ == "__main__":
    unittest.main()

