#!/usr/bin/env bash
# C5: a second `up` against a running deployment is a no-op for stored memories, and
# does not rotate the token. Run from the repository root, after `memcp up`.
set -euo pipefail

backend="${1:-sqlite}"
shift || true
# Everything after the backend is passed through to the second `up`. A backend whose
# engine needs a model endpoint refuses without it (C3), so the re-run has to be
# spelled the same way the first one was or this asserts the refusal, not idempotency.
extra=("$@")
dir=".memcp"
token="$(grep '^MEMCP_TOKEN=' "$dir/.env" | cut -d= -f2-)"
port=8080
url="http://127.0.0.1:${port}/mcp"

call() {
  curl -sS -X POST "$url" \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -H "Authorization: Bearer ${token}" \
    -d "$1" | grep '^data:' | head -1 | cut -c6-
}

marker="idempotency-$(date +%s)"
call "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"add_memory\",\"arguments\":{\"content\":\"${marker}\",\"infer\":false}}}" > /dev/null
echo "stored ${marker}"

python -m memcp up --backend "$backend" --dir "$dir" --memcp-source . --timeout 600 \
  "${extra[@]+"${extra[@]}"}"

after="$(grep '^MEMCP_TOKEN=' "$dir/.env" | cut -d= -f2-)"
if [ "$token" != "$after" ]; then
  echo "FAIL: the token rotated on a second up"
  exit 1
fi
echo "token unchanged"

if [ "$backend" = "in_memory" ]; then
  echo "in_memory is documented as losing everything on restart — not asserting recall"
  exit 0
fi

found="$(call "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"search_memory\",\"arguments\":{\"query\":\"${marker}\"}}}")"
if ! grep -q "$marker" <<<"$found"; then
  echo "FAIL: the memory stored before the second up is gone"
  echo "$found"
  exit 1
fi
echo "memory survived the second up"
