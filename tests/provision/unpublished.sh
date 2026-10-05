#!/usr/bin/env bash
# JAR-559: a deployment with no host port, under a platform that routes into the
# container. Run from the repository root on a host with Docker.
#
# What it asserts, in order: nothing is published, something on the external network
# still reaches memcp by name, the bearer gate still answers 401 from there, the
# first-memory check runs by its other route, and switching the flag on an existing
# deployment keeps the memories and the token.
set -euo pipefail

dir=".memcp-unpublished"
net="memcp-ci-edge"
project="memcp-unpublished"
marker="unpublished-$(date +%s)"

cleanup() {
  python -m memcp down --dir "$dir" --volumes --project "$project" >/dev/null 2>&1 || true
  docker network rm "$net" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker network create "$net" >/dev/null

published_up() {
  python -m memcp up --dir "$dir" --project "$project" --memcp-source . \
    --port 8099 --timeout 600
}

token() { grep '^MEMCP_TOKEN=' "$dir/.env" | cut -d= -f2-; }

host_call() {
  curl -sS -X POST "http://127.0.0.1:8099/mcp" \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -H "Authorization: Bearer $(token)" \
    -d "$1" | grep '^data:' | head -1 | cut -c6-
}

echo "--- a normal published deployment, with one memory in it"
published_up
first_token="$(token)"
host_call "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"add_memory\",\"arguments\":{\"content\":\"${marker}\",\"infer\":false}}}" >/dev/null
echo "stored ${marker} over the published port"

echo "--- the same deployment, publishing nothing"
python -m memcp up --dir "$dir" --project "$project" --memcp-source . \
  --no-publish --network "$net" --external-url "http://memcp:8080" \
  --smoke --timeout 600

if grep -q "ports:" "$dir/docker-compose.yml"; then
  echo "FAIL: the compose file still publishes a port"; exit 1
fi
mapped="$(docker ps --filter "label=com.docker.compose.project=${project}" --format '{{.Ports}}')"
if grep -q -- '->' <<<"$mapped"; then
  echo "FAIL: a container still maps a host port: $mapped"; exit 1
fi
echo "no host port is published"

if curl -sS --max-time 5 "http://127.0.0.1:8099/mcp" >/dev/null 2>&1; then
  echo "FAIL: the host still reached the deployment"; exit 1
fi
echo "the host cannot reach it, which is the point"

echo "--- something on the external network reaches it, the way a platform's router would"
image="$(docker compose -f "$dir/docker-compose.yml" images -q memcp | head -1)"
docker run --rm --network "$net" --entrypoint python "$image" -c "
import json, sys, urllib.error, urllib.request

health = urllib.request.urlopen('http://memcp:8080/health', timeout=10)
assert health.status == 200, health.status
print('reached memcp:8080/health from the external network')

body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                   'params': {'protocolVersion': '2025-03-26', 'capabilities': {},
                              'clientInfo': {'name': 'ci', 'version': '1'}}}).encode()
request = urllib.request.Request('http://memcp:8080/mcp', data=body, headers={
    'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'})
try:
    urllib.request.urlopen(request, timeout=10)
except urllib.error.HTTPError as e:
    assert e.code == 401, f'expected 401 without a token, got {e.code}'
    print('the bearer gate answers 401 on that route too')
else:
    sys.exit('FAIL: an unauthenticated request was served')
"

echo "--- the first-memory check, by the route that exists"
python -m memcp verify --dir "$dir" --project "$project"

echo "--- publishing again, and the memory is still there"
published_up
if [ "$(token)" != "$first_token" ]; then
  echo "FAIL: the token rotated across the flag change"; exit 1
fi
found="$(host_call "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"search_memory\",\"arguments\":{\"query\":\"${marker}\"}}}")"
if ! grep -q "$marker" <<<"$found"; then
  echo "FAIL: the memory stored before the flag changed is gone"; echo "$found"; exit 1
fi
echo "token unchanged and the memory survived both switches"
