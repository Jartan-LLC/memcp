"""An OpenAI-compatible stand-in so CI can run a real cognee with no API key.

Cognee needs two models to store anything: an LLM that extracts a knowledge graph from
the text, and an embedder that makes it retrievable. Both are OpenAI-shaped HTTP calls
and cognee honours LLM_ENDPOINT and EMBEDDING_ENDPOINT, so pointing it here gives CI a
real cognee server, a real Kuzu graph, a real LanceDB index and a real REST surface
with no secret and no network egress.

What this is and is not, precisely, because the distinction is the whole reason the
file is readable:

- **The graph is real.** Cognee builds it, stores it in Kuzu, and serves it back
  through its own dataset-graph endpoint. The nodes are graph nodes and the edges are
  graph edges. `memory_entities` on this stack is not a synthetic node with a count on
  it — it is what cognee's pipeline produced.
- **The extractor is not a language model.** It reads the schema cognee's prompt asks
  for and answers it deterministically: proper-noun phrases become entities, and
  consecutive ones become an edge. So CI proves the adapter's contract and cognee's
  pipeline, and proves nothing whatever about extraction *quality*.
- **Embeddings are token-hash bags**, the same construction ci/mem0 uses. Cosine
  similarity between two texts is their normalised word overlap, which makes retrieval
  lexical and deterministic — what a portability round trip needs, and deliberately not
  a semantic-quality claim.

Anything that depends on real extraction or real embedding quality has to run against a
real provider. Nothing in this repository has measured that yet.

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
PROPER_NOUN_RE = re.compile(r"\b([A-Z][a-zA-Z0-9]*(?:\s+[A-Z][a-zA-Z0-9]*)*)\b")

# Instructor asks for JSON by putting the pydantic schema in the system prompt after
# this phrase, rather than in response_format. Everything the stub answers is driven
# off the schema it finds there.
SCHEMA_MARKER = "matching this schema:"

# Words that start a sentence and are not entities. Without this every "The" begins a
# node, which makes the graph noise rather than a graph.
STOPWORDS = frozenset({"the", "this", "that", "a", "an", "it", "they", "there", "these"})


def embed(text: str) -> list[float]:
    """Hash each word into one of DIMENSIONS buckets, then L2-normalise."""
    vector = [0.0] * DIMENSIONS
    for token in TOKEN_RE.findall(text.lower()):
        digest = hashlib.sha1(token.encode("utf-8")).digest()
        vector[int.from_bytes(digest[:8], "big") % DIMENSIONS] += 1.0
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0.0:
        # An empty or symbol-only input still needs a unit vector; a zero vector makes
        # every similarity NaN.
        vector[0] = 1.0
        return vector
    return [v / norm for v in vector]


def find_schema(system_prompt: str) -> dict[str, Any] | None:
    """The JSON schema instructor embedded in the prompt, if there is one."""
    marker = system_prompt.find(SCHEMA_MARKER)
    if marker < 0:
        return None
    tail = system_prompt[marker + len(SCHEMA_MARKER) :]
    start = tail.find("{")
    if start < 0:
        return None
    depth = 0
    for index, character in enumerate(tail[start:], start):
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(tail[start : index + 1])
                except json.JSONDecodeError:
                    return None
    return None


def entity_names(text: str) -> list[str]:
    names: list[str] = []
    for phrase in PROPER_NOUN_RE.findall(text):
        name = phrase.strip()
        if len(name) < 3 or name.lower() in STOPWORDS or name in names:
            continue
        names.append(name)
    return names


def knowledge_graph(text: str) -> dict[str, Any]:
    """Proper nouns as entities, adjacency as relationships."""
    names = entity_names(text)

    def node_id(name: str) -> str:
        return name.lower().replace(" ", "_")

    return {
        "nodes": [
            {"id": node_id(n), "name": n, "type": "Entity", "description": n} for n in names
        ],
        "edges": [
            {
                "source_node_id": node_id(a),
                "target_node_id": node_id(b),
                "relationship_name": "related_to",
                "description": f"{a} appears with {b} in the same memory.",
            }
            for a, b in zip(names, names[1:], strict=False)
        ],
    }


def instance(node: dict[str, Any], root: dict[str, Any], text: str) -> Any:
    """The smallest value that satisfies a schema node, filled from the input text."""
    if "$ref" in node:
        return instance(root.get("$defs", {}).get(node["$ref"].split("/")[-1], {}), root, text)
    if "anyOf" in node:
        return instance(node["anyOf"][0], root, text)
    kind = node.get("type")
    if kind == "object":
        return {
            name: instance(node.get("properties", {}).get(name, {}), root, text)
            for name in node.get("required", [])
        }
    if kind == "array":
        return []
    if kind == "integer":
        return 0
    if kind == "number":
        return 0.0
    if kind == "boolean":
        return False
    if kind == "null":
        return None
    return text


def answer(schema: dict[str, Any] | None, user_text: str) -> str:
    if schema is None:
        return user_text
    if schema.get("title") == "KnowledgeGraph":
        return json.dumps(knowledge_graph(user_text))
    return json.dumps(instance(schema, schema, user_text))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass

    def _send(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's spelling
        if self.path.rstrip("/").endswith("/models"):
            self._send(200, {"object": "list", "data": [{"id": "gpt-4o-mini", "object": "model"}]})
            return
        if self.path.rstrip("/").endswith("/health"):
            self._send(200, {"status": "ok"})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's spelling
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            payload = {}

        if "embeddings" in self.path:
            inputs = payload.get("input", "")
            texts = [inputs] if isinstance(inputs, str) else list(inputs)
            self._send(
                200,
                {
                    "object": "list",
                    "data": [
                        {"object": "embedding", "index": i, "embedding": embed(str(t))}
                        for i, t in enumerate(texts)
                    ],
                    "model": payload.get("model", "fake-embed"),
                    "usage": {"prompt_tokens": 1, "total_tokens": 1},
                },
            )
            return

        if "chat/completions" in self.path:
            messages = payload.get("messages", [])
            system = "\n".join(m.get("content") or "" for m in messages if m.get("role") == "system")
            user = "\n".join(m.get("content") or "" for m in messages if m.get("role") == "user")
            self._send(
                200,
                {
                    "id": "chatcmpl-fake",
                    "object": "chat.completion",
                    "created": 0,
                    "model": payload.get("model", "fake-chat"),
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": answer(find_schema(system), user),
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            )
            return

        self._send(404, {"error": "not found"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()  # noqa: S104
