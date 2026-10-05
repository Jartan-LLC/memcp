#!/usr/bin/env bash
# C3: `memcp up --backend mem0` with no provider key must stop before it creates
# anything, and must name the variable it needs.
set -uo pipefail

out="$(env -u OPENAI_API_KEY python -m memcp up --backend mem0 --dir .refusal --memcp-source . 2>&1)"
code=$?

if [ "$code" -eq 0 ]; then
  echo "FAIL: it started without a provider key"
  exit 1
fi
if ! grep -q "OPENAI_API_KEY" <<<"$out"; then
  echo "FAIL: the failure did not name the variable"
  echo "$out"
  exit 1
fi
if [ -e .refusal/docker-compose.yml ]; then
  echo "FAIL: it wrote a compose file before failing"
  exit 1
fi
echo "refused, named OPENAI_API_KEY, created nothing"
