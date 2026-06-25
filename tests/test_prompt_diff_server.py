import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import urlopen

from prompt_evolver.prompt_diff_server import (
    build_prompt_diff_payload,
    create_prompt_diff_server,
    find_prompt_diff_demo_file,
)


class PromptDiffServerTests(unittest.TestCase):
    def test_build_prompt_diff_payload_reads_markdown_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = root / "original.md"
            revised = root / "revised.md"
            original.write_text("# Original\nold rule\n", encoding="utf-8")
            revised.write_text("# Revised\nnew rule\n", encoding="utf-8")

            payload = build_prompt_diff_payload(original, revised)

        self.assertEqual(payload["schema_version"], "1.0")
        self.assertEqual(payload["original"]["name"], "original.md")
        self.assertEqual(payload["revised"]["name"], "revised.md")
        self.assertIn("old rule", payload["original"]["text"])
        self.assertIn("new rule", payload["revised"]["text"])

    def test_server_serves_demo_and_prompt_payload(self):
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = root / "original.md"
            revised = root / "revised.md"
            original.write_text("# Original\nold rule\n", encoding="utf-8")
            revised.write_text("# Revised\nnew rule\n", encoding="utf-8")
            server, info = create_prompt_diff_server(original, revised, port=0, cwd=repo_root)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                page = urlopen(info.url, timeout=5).read().decode("utf-8")
                payload = json.loads(urlopen(info.url + "api/prompts", timeout=5).read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

        self.assertIn("Prompt Diff Viewer", page)
        self.assertEqual(payload["original"]["name"], "original.md")
        self.assertEqual(payload["revised"]["name"], "revised.md")

    def test_find_prompt_diff_demo_file_from_repo_root(self):
        repo_root = Path(__file__).resolve().parents[1]
        demo = find_prompt_diff_demo_file(repo_root)

        self.assertEqual(demo.name, "index.html")
        self.assertIn("prompt-diff-viewer", str(demo))


if __name__ == "__main__":
    unittest.main()
