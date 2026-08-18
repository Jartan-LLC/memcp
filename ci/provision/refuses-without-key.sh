#!/usr/bin/env bash
# C3: a backend whose engine needs a model provider must stop before it creates
# anything when no key is supplied, and must name the variable it needs.
set -uo pipefail

check() {
  backend="$1"
  variable="$2"
  directory=".refusal-$backend"

  out="$(env -u OPENAI_API_KEY -u COGNEE_LLM_API_KEY \
    python -m memcp up --backend "$backend" --dir "$directory" --memcp-source . 2>&1)"
  code=$?

  if [ "$code" -eq 0 ]; then
    echo "FAIL($backend): it started without a provider key"
    exit 1
  fi
  if ! grep -q "$variable" <<<"$out"; then
    echo "FAIL($backend): the failure did not name $variable"
    echo "$out"
    exit 1
  fi
  if [ -e "$directory/docker-compose.yml" ]; then
    echo "FAIL($backend): it wrote a compose file before failing"
    exit 1
  fi
  echo "$backend: refused, named $variable, created nothing"
}

check mem0 OPENAI_API_KEY
check cognee COGNEE_LLM_API_KEY
