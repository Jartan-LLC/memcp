"""An OpenAI-compatible stand-in so CI can run a real mem0 with no API key.

mem0 needs an embedder to store anything and an LLM to extract facts. Both are
OpenAI-shaped HTTP calls, and mem0 honours OPENAI_BASE_URL, so pointing it here
gives CI a real mem0 server, a real pgvector store and a real REST surface with no
secret and no network egress.

What this is not: an embedding model. Embeddings are token-hash bags — cosine
similarity between two texts is their normalised word overlap. That makes retrieval
lexical and deterministic, which is what a portability round trip needs (the same
query finds the same memory) and is deliberately not a semantic-quality claim.
Anything that depends on real embedding quality has to run against a real provider.

Fact extraction is stubbed to "extracted nothing", which is a documented legal
outcome of mem0's add(infer=True) rather than a fake result. CI therefore proves the
adapter's contract, not mem0's extraction.

Standard library only, so the image is python:3.12-slim with nothing installed.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

DIMENSIONS = 1536
TOKEN_RE = re.compile(r"[a-z0-9]+")

# mem0's add(infer=True) parses this and treats an empty list as "nothing to store".
EXTRACTION_REPLY = json.dumps({"memory": []})


def embed(text: str) -> list[float]:
    """Hash each word into one of DIMENSIONS buckets, then L2-normalise."""
    vector = [0.0] * DIMENSIONS
    for token in TOKEN_RE.findall(text.lower()):
        digest = hashlib.sha1(token.encode("utf-8")).digest()
        vector[int.from_bytes(digest[:8], "big") % DIMENSIONS] += 1.0
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0.0:
        # An empty or symbol-only input still needs a unit vector; pgvector rejects
        # nothing, but a zero vector makes every similarity NaN.
        vector[0] = 1.0
        return vector
    return [v / norm for v in vector]


def _texts(payload: Any) -> list[str]:
    value = payload.get("input", "")
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v if isinstance(v, str) else json.dumps(v) for v in value]
    return [str(value)]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, status: int, body: dict[str, Any]) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's naming
        if self.path in ("/health", "/healthz"):
            self._send(200, {"status": "ok"})
            return
        if self.path.rstrip("/") == "/v1/models":
            self._send(
                200,
                {
                    "object": "list",
                    "data": [
                        {"id": "fake-embed", "object": "model", "owned_by": "memcp-ci"},
                        {"id": "fake-chat", "object": "model", "owned_by": "memcp-ci"},
                    ],
                },
            )
            return
        self._send(404, {"error": {"message": f"no such path {self.path}", "type": "not_found"}})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's naming
        payload = self._read()
        path = self.path.rstrip("/")
        if path == "/v1/embeddings":
            texts = _texts(payload)
            self._send(
                200,
                {
                    "object": "list",
                    "model": payload.get("model", "fake-embed"),
                    "data": [
                        {"object": "embedding", "index": i, "embedding": embed(t)}
                        for i, t in enumerate(texts)
                    ],
                    "usage": {"prompt_tokens": 0, "total_tokens": 0},
                },
            )
            return
        if path == "/v1/chat/completions":
            self._send(
                200,
                {
                    "id": "chatcmpl-memcp-ci",
                    "object": "chat.completion",
                    "created": 0,
                    "model": payload.get("model", "fake-chat"),
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": EXTRACTION_REPLY},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                },
            )
            return
        self._send(404, {"error": {"message": f"no such path {self.path}", "type": "not_found"}})

    def log_message(self, fmt: str, *args: Any) -> None:
        if os.environ.get("FAKE_OPENAI_VERBOSE"):
            super().log_message(fmt, *args)


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)  # noqa: S104 - container-local
    print(f"fake-openai listening on :{port}, {DIMENSIONS} dimensions", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
