#!/usr/bin/env bash
# Bring up a disposable cognee for conformance runs, then print the environment the
# adapter needs. The same command CI runs.
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

docker compose up -d --build --wait

cat <<'ENV'

cognee is up. Point the conformance suite at it with:

  export COGNEE_API_BASE=http://127.0.0.1:8890
  export COGNEE_TENANT_SECRET=memcp-conformance-tenant-secret
  python -m memcp.conformance

Tear it down with ci/cognee/down.sh.
ENV
