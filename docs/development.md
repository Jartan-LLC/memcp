# Development

Setup and the check loop (`make check`) are in [CONTRIBUTING.md](../CONTRIBUTING.md).

## Backend conformance

Any `MemoryBackend` implementation, in this repository or not, is held to one suite:

```bash
python -m memcp.conformance
```

It prints, per backend, every capability it implements and every one it does not. A
backend that declares a capability and fails its tests fails the run; only an
undeclared capability skips.

Switching backends is held to the same bar. The suite migrates a 24-memory corpus
across three scopes between every pair of backends and asserts that content, scope
and retrieval by the original query all survive. What does not survive is written
down per pair in [portability.md](portability.md) and asserted against — an
undocumented loss fails CI rather than passing quietly.

Both run on every pull request against a real mem0, stood up locally with no API key
(`tests/mem0/up.sh`).

[conformance.md](conformance.md) covers backend selection, out-of-tree adapters, and
what the suite deliberately does not check.

## See also

- [reference.md](reference.md) — environment variables, tool surface, known limitations
- [deployment.md](deployment.md) — what `memcp up` provisions and how it is secured
