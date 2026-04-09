"""Lightweight HTTP proxy for Claude CLI.

Runs on the host machine and forwards prompts from Docker containers
to the locally installed `claude -p` CLI. Containers reach this proxy
via http://host.docker.internal:8484.

Usage:
    python scripts/claude_proxy.py              # default port 8484
    CLAUDE_PROXY_PORT=9000 python scripts/claude_proxy.py

Endpoints:
    POST /ask   — {"prompt": "..."} → {"output": "..."} or {"error": "..."}
    GET  /health — {"status": "ok"}
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

_PORT = int(os.environ.get("CLAUDE_PROXY_PORT", "8484"))
_CLI_TIMEOUT = int(os.environ.get("CLAUDE_PROXY_TIMEOUT", "300"))
# Working directory for claude CLI — controls which CLAUDE.md is loaded.
# Set to a directory containing a CLAUDE.md to customize Claude's behavior.
_WORKDIR = os.environ.get("CLAUDE_PROXY_WORKDIR", os.getcwd())


class ClaudeProxyHandler(BaseHTTPRequestHandler):
    """Handle /ask and /health requests."""

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json_response(200, {"status": "ok", "workdir": _WORKDIR})
        else:
            self._json_response(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/ask":
            self._json_response(404, {"error": "not found"})
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._json_response(400, {"error": "empty body"})
            return

        try:
            body = json.loads(self.rfile.read(content_length))
        except (json.JSONDecodeError, ValueError) as e:
            self._json_response(400, {"error": f"invalid JSON: {e}"})
            return

        prompt = body.get("prompt")
        if not prompt:
            self._json_response(400, {"error": "missing 'prompt' field"})
            return

        model = body.get("model")
        max_tokens = body.get("max_tokens")
        # Per-request workdir override — falls back to global _WORKDIR.
        workdir = body.get("workdir", _WORKDIR)

        cmd = ["claude", "-p", prompt, "--output-format", "text"]
        if model:
            cmd.extend(["--model", model])
        if max_tokens:
            cmd.extend(["--max-tokens", str(max_tokens)])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_CLI_TIMEOUT,
                cwd=workdir,
            )
        except subprocess.TimeoutExpired:
            self._json_response(504, {"error": f"claude CLI timed out after {_CLI_TIMEOUT}s"})
            return
        except FileNotFoundError:
            self._json_response(502, {"error": "claude CLI not found on host PATH"})
            return
        except OSError as e:
            self._json_response(502, {"error": f"failed to run claude: {e}"})
            return

        if result.returncode != 0:
            self._json_response(502, {
                "error": f"claude exited with code {result.returncode}",
                "stderr": result.stderr[:2000] if result.stderr else "",
            })
            return

        self._json_response(200, {"output": result.stdout})

    def _json_response(self, status: int, data: dict) -> None:
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        """Structured log output."""
        print(f"[claude-proxy] {args[0]}", flush=True)


def main() -> None:
    server = HTTPServer(("0.0.0.0", _PORT), ClaudeProxyHandler)
    print(f"[claude-proxy] listening on 0.0.0.0:{_PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[claude-proxy] shutting down", flush=True)
        server.shutdown()


if __name__ == "__main__":
    main()
