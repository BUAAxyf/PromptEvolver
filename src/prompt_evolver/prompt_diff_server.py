from __future__ import annotations

import json
import mimetypes
import errno
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .errors import ValidationError

DEFAULT_PROMPT_DIFF_PORT = 8765


@dataclass(frozen=True)
class PromptDiffServerInfo:
    url: str
    host: str
    port: int
    original_prompt: Path
    revised_prompt: Path
    demo_file: Path


def build_prompt_diff_payload(original_prompt: Path, revised_prompt: Path) -> dict[str, Any]:
    if not original_prompt.exists() or not original_prompt.is_file():
        raise ValidationError(f"original prompt does not exist: {original_prompt}")
    if not revised_prompt.exists() or not revised_prompt.is_file():
        raise ValidationError(f"revised prompt does not exist: {revised_prompt}")
    return {
        "schema_version": "1.0",
        "original": {
            "path": str(original_prompt),
            "name": original_prompt.name,
            "text": original_prompt.read_text(encoding="utf-8"),
        },
        "revised": {
            "path": str(revised_prompt),
            "name": revised_prompt.name,
            "text": revised_prompt.read_text(encoding="utf-8"),
        },
    }


def find_prompt_diff_demo_file(cwd: Path | None = None) -> Path:
    roots = []
    if cwd is not None:
        roots.append(cwd)
    roots.append(Path.cwd())
    roots.append(Path(__file__).resolve().parents[2])
    for root in roots:
        candidate = root / "demos" / "prompt-diff-viewer" / "index.html"
        if candidate.exists():
            return candidate
    raise ValidationError("prompt diff demo file not found: demos/prompt-diff-viewer/index.html")


def make_prompt_diff_handler(
    demo_file: Path,
    payload: dict[str, Any],
) -> type[BaseHTTPRequestHandler]:
    demo_bytes = demo_file.read_bytes()
    payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    class PromptDiffHandler(BaseHTTPRequestHandler):
        server_version = "PromptEvolverPromptDiff/1.0"

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            parsed = urlparse(self.path)
            if parsed.path in {"/", "/index.html"}:
                self._send_bytes(demo_bytes, "text/html; charset=utf-8")
                return
            if parsed.path == "/api/prompts":
                self._send_bytes(payload_bytes, "application/json; charset=utf-8")
                return
            if parsed.path == "/health":
                self._send_bytes(b'{"ok":true}', "application/json; charset=utf-8")
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_bytes(self, body: bytes, content_type: str) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return PromptDiffHandler


def create_prompt_diff_server(
    original_prompt: Path,
    revised_prompt: Path,
    host: str = "127.0.0.1",
    port: int = DEFAULT_PROMPT_DIFF_PORT,
    cwd: Path | None = None,
) -> tuple[ThreadingHTTPServer, PromptDiffServerInfo]:
    payload = build_prompt_diff_payload(original_prompt, revised_prompt)
    demo_file = find_prompt_diff_demo_file(cwd)
    handler = make_prompt_diff_handler(demo_file, payload)
    server = _bind_server(handler, host, port)
    bound_port = int(server.server_address[1])
    return server, PromptDiffServerInfo(
        url=f"http://{host}:{bound_port}/",
        host=host,
        port=bound_port,
        original_prompt=original_prompt,
        revised_prompt=revised_prompt,
        demo_file=demo_file,
    )


def open_prompt_diff_browser(url: str) -> bool:
    return webbrowser.open(url)


def guess_content_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def _bind_server(
    handler: type[BaseHTTPRequestHandler],
    host: str,
    port: int,
) -> ThreadingHTTPServer:
    if port == 0:
        return ThreadingHTTPServer((host, 0), handler)
    for candidate in range(port, port + 50):
        try:
            return ThreadingHTTPServer((host, candidate), handler)
        except OSError as exc:
            if not _is_port_unavailable(exc):
                raise
    raise ValidationError(f"no available port found from {port} to {port + 49}")


def _is_port_unavailable(exc: OSError) -> bool:
    return exc.errno == errno.EADDRINUSE
