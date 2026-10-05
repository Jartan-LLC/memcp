#!/bin/bash
# An unset variable is a typo, so it stops the script. No set -e or pipefail: each
# install step is best-effort and reports its own failure, and the rest still runs.
set -u

echo "Setting up development environment..."

# Pinned from ci/requirements.txt so the container matches CI, and bootstrapped
# with pip because that is what the python devcontainer feature ships.
echo "Installing uv..."
uv_pin=$(sed -n 's/^\(uv==[^[:space:]]*\).*/\1/p' ci/requirements.txt 2>/dev/null | head -1)
if [ -n "$uv_pin" ]; then
    pip install "$uv_pin" || echo "Warning: uv install failed ($uv_pin)" >&2
else
    echo "Warning: no pinned uv in ci/requirements.txt; Python installs below will fail" >&2
fi

# Into the system Python: containerEnv sets UV_SYSTEM_PYTHON, and the Makefile installs there
# when no venv exists.
echo "Installing project dependencies (make install)..."
make_install_failed=false
make install || make_install_failed=true

# Reported last so the install output doesn't scroll it away. Not fatal: a failed
# postCreateCommand skips postStart, which fixes the Docker socket and the Codespaces
# path.
if $make_install_failed; then
    echo "ERROR: Project dependency install failed (see make's output above). Run 'make install' to retry." >&2
fi

echo "Development environment setup complete!"
