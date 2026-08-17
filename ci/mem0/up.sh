#!/usr/bin/env bash
# Bring up a disposable mem0 for conformance runs, then print the environment the
# adapter needs. The same command CI runs.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$here/fetch.sh"
cd "$here"

docker compose up -d --build --wait

cat <<'ENV'

mem0 is up. Point the conformance suite at it with:

  export MEM0_API_BASE=http://127.0.0.1:8888
  export MEM0_API_KEY=memcp-conformance-admin-key
  python -m memcp.conformance

Tear it down with ci/mem0/down.sh.
ENV
