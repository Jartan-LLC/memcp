#!/usr/bin/env bash
# Prints "true" when tag $1 is the highest stable release among the repository's tags,
# else "false". Only X.Y.Z is stable: 1.0.0-rc1 and 1.0.0rc1 never take latest.
set -euo pipefail
version="${1#v}"
highest="$(git tag -l 'v*' | sed 's/^v//' | { grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' || true; } | sort -V | tail -1)"
if [ "$version" = "$highest" ]; then echo true; else echo false; fi
