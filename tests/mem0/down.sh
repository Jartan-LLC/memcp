#!/usr/bin/env bash
# Tear down the conformance mem0 stack, volumes included. Nothing here is durable.
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
docker compose down -v --remove-orphans
