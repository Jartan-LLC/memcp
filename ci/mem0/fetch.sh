#!/usr/bin/env bash
# Clone Jartan-LLC/mem0 at the pinned SHA into ci/mem0/.mem0. Public repository, so
# no credential is involved.
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

repo="${MEM0_REPO:-https://github.com/Jartan-LLC/mem0.git}"
pin="$(tr -d '[:space:]' < mem0.pin)"

if [ ! -d .mem0/.git ]; then
  git clone --filter=blob:none --no-checkout "$repo" .mem0
fi
git -C .mem0 fetch --depth 1 origin "$pin"
git -C .mem0 checkout --force "$pin"
echo "mem0 checked out at $pin"
