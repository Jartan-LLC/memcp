#!/bin/bash

echo "Setting up development environment..."

# uv installs every Python dependency below, in place of pip. Its version comes
# from ci/requirements.txt — the one line CI's setup-uv reads too — so the
# container, the Dockerfile and the runner never drift apart, and Dependabot's
# "/ci" entry moves all three at once. Bootstrapped with pip because pip is what
# the python devcontainer feature ships; nothing else here uses it.
echo "Installing uv..."
uv_pin=$(sed -n 's/^\(uv==[^[:space:]]*\).*/\1/p' ci/requirements.txt 2>/dev/null | head -1)
if [ -n "$uv_pin" ]; then
    pip install "$uv_pin" || echo "Warning: uv install failed ($uv_pin)" >&2
else
    echo "Warning: no pinned uv in ci/requirements.txt; Python installs below will fail" >&2
fi

# --system: the container is the isolation, so packages go to its interpreter
# rather than a venv. uv refuses a non-venv target without this.
echo "Installing Python dependencies..."
while IFS= read -r -d '' req_file; do
    echo "  Installing from $req_file..."
    uv pip install --system -r "$req_file"
done < <(find . -name "requirements.txt" -type f -print0 2>/dev/null)

# Install Python dependencies from all pyproject.toml files (editable installs)
while IFS= read -r -d '' pyproject_file; do
    dir=$(dirname "$pyproject_file")
    echo "  Installing from $dir..."
    uv pip install --system -e "$dir"
done < <(find . -name "pyproject.toml" -type f -print0 2>/dev/null)

# Fix ownership on Claude volume mount (fresh volumes are root-owned)
sudo chown -R vscode:vscode /home/vscode/.claude || true

gh auth status 2>/dev/null || echo "Note: Run 'gh auth login' to enable GitHub CLI (gh pr, gh issue, etc.)"

echo "Development environment setup complete!"
