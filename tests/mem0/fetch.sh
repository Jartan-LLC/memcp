#!/usr/bin/env bash
# Clone mem0ai/mem0 at the pinned SHA (release v2.2.1) into tests/mem0/.mem0, and pin
# the mem0ai engine the server installs to the same release: the server's
# requirements.txt only sets a floor, so an unpinned build picks up whatever PyPI has.
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

repo="${MEM0_REPO:-https://github.com/mem0ai/mem0.git}"
pin="$(tr -d '[:space:]' < mem0.pin)"
engine="2.2.1" # the mem0ai release matching mem0.pin; move the two together

if [ ! -d .mem0/.git ]; then
  git clone --filter=blob:none --no-checkout "$repo" .mem0
fi
git -C .mem0 remote set-url origin "$repo"
git -C .mem0 fetch --depth 1 origin "$pin"
git -C .mem0 checkout --force "$pin"
sed -i "s/^mem0ai>=.*/mem0ai==$engine/" .mem0/server/requirements.txt
grep -qx "mem0ai==$engine" .mem0/server/requirements.txt
echo "mem0 checked out at $pin, engine mem0ai==$engine"
