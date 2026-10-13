# Contributing

## Setup

```bash
uv venv        # skip in the devcontainer or with an environment already active
make install
```

`make install` installs the project's dependencies and wires the pre-commit hook; rerun it
after dependencies change. Make targets find the environment themselves; activate `.venv`
only if you want `pytest` directly on your shell's PATH.

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).
`make lint` runs the [pre-commit](https://pre-commit.com/) hooks; some need
Docker (actionlint, lychee) and Node (markdownlint) — the devcontainer has both.

## Verify before opening a PR

```bash
make check
```

Runs CI's lint, typecheck, test, build and audit checks. CI also runs the tests on each
supported Python, builds the Docker image, runs the conformance suite against a real mem0
([tests/mem0/README.md](tests/mem0/README.md)), and builds the dev container when its
inputs change. Everything but the advisory audit must pass before merge.

## Run memcp from a checkout

`docker-compose.build.yml` builds the image from the working tree. It uses the same port
as a release deployment.

1. Stop any running release deployment.
2. Create `.env`:

   ```bash
   cp .env.example .env && chmod 600 .env
   ```

3. In `.env`, set `MEMCP_AUTH_TOKENS=<token>:<your-name>` with a token from
   `openssl rand -hex 32`.
4. Build and start it:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build --wait
   ```

## Backend conformance suite

`tests/conformance` holds every backend to the same contract: the required methods,
tenant isolation on each, every optional capability the backend declares (and a
`NotImplementedError` for each it doesn't), and a round trip between every pair of
backends that checks content, scope and retrieval survive. `pytest tests/conformance`
runs it against `in_memory` and `sqlite`, as `make test` does. To include mem0, start a
local one first ([tests/mem0/README.md](tests/mem0/README.md)).

A new backend needs an entry in each of:

- `BACKENDS` and `_open` in `tests/conformance/conftest.py`
- `BACKEND_PARAMS` and `_config` in `tests/test_tenant_isolation.py`
- `EXPECTED_TOOLS` and the `builders` in `_registered_tools` in `tests/test_tool_surface.py`

## Conventions

- Commits follow [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `docs:`, `refactor:`, `chore:`).
- User-facing changes go in `CHANGELOG.md` under `## [Unreleased]`.
- `docs/tool-surface.json` freezes the MCP tool names, argument schemas and annotations.
  To change them, state in the PR what breaks for a connected client, then run
  `python -m memcp.toolsurface --write`.
- Report security issues privately via [SECURITY.md](.github/SECURITY.md), not a public issue.
