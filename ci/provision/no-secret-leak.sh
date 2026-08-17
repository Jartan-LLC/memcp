#!/usr/bin/env bash
# G4: the minted token lives in one 0600 file and nowhere else git can see.
set -euo pipefail

dir=".memcp"
token="$(grep '^MEMCP_TOKEN=' "$dir/.env" | cut -d= -f2-)"

mode="$(stat -c '%a' "$dir/.env")"
if [ "$mode" != "600" ]; then
  echo "FAIL: $dir/.env is mode $mode, expected 600"
  exit 1
fi

if grep -rq -- "$token" --exclude-dir=.git --exclude=".env" .; then
  echo "FAIL: the minted token appears outside $dir/.env:"
  grep -rl -- "$token" --exclude-dir=.git --exclude=".env" .
  exit 1
fi

if [ -n "$(git status --porcelain --untracked-files=all | grep -v '^!!' | grep "$dir" || true)" ]; then
  echo "FAIL: git can see files inside $dir"
  git status --porcelain --untracked-files=all | grep "$dir"
  exit 1
fi

echo "the token is in $dir/.env (0600) and nowhere else; git sees nothing in $dir"
