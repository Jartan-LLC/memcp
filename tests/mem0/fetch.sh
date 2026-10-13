#!/usr/bin/env bash
# Clone mem0ai/mem0 at the pinned SHA into tests/mem0/.mem0, and pin the mem0ai engine the
# server installs to that commit's own version: the server's requirements.txt only sets a
# floor, so an unpinned build picks up whatever PyPI has.
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

repo="${MEM0_REPO:-https://github.com/mem0ai/mem0.git}"
pin="$(tr -d '[:space:]' < mem0.pin)"

if [ ! -d .mem0/.git ]; then
  git clone --filter=blob:none --no-checkout "$repo" .mem0
fi
git -C .mem0 remote set-url origin "$repo"
git -C .mem0 fetch --depth 1 origin "$pin"
git -C .mem0 checkout --force "$pin"

engine="$(sed -n '/^\[project\]$/,/^\[/s/^version = "\(.*\)"$/\1/p' .mem0/pyproject.toml)"
if [ -z "$engine" ]; then
  echo "no version in .mem0/pyproject.toml at $pin" >&2
  exit 1
fi
reqs=.mem0/server/requirements.txt
sed "s/^mem0ai>=.*/mem0ai==$engine/" "$reqs" > "$reqs.tmp"
mv "$reqs.tmp" "$reqs"
if ! grep -qx "mem0ai==$engine" "$reqs"; then
  echo "could not pin mem0ai in $reqs: its mem0ai line changed upstream" >&2
  exit 1
fi
echo "mem0 checked out at $pin, engine mem0ai==$engine"
