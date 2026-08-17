#!/bin/bash

echo "Setting up development environment..."

# Install Python dependencies from all requirements.txt files
echo "Installing Python dependencies..."
while IFS= read -r -d '' req_file; do
    echo "  Installing from $req_file..."
    pip install -r "$req_file"
done < <(find . -name "requirements.txt" -type f -print0 2>/dev/null)

# Install Python dependencies from all pyproject.toml files (editable installs)
while IFS= read -r -d '' pyproject_file; do
    dir=$(dirname "$pyproject_file")
    echo "  Installing from $dir..."
    pip install -e "$dir"
done < <(find . -name "pyproject.toml" -type f -print0 2>/dev/null)

# Fix ownership on Claude volume mount (fresh volumes are root-owned)
sudo chown -R vscode:vscode /home/vscode/.claude || true

gh auth status 2>/dev/null || echo "Note: Run 'gh auth login' to enable GitHub CLI (gh pr, gh issue, etc.)"

echo "Development environment setup complete!"
