#!/bin/bash
# Checks that the Features and post-create.sh set this container up; both only report
# their failures. CI runs it in a fresh container. A Feature's checks run only when its
# image marker exists, so removing a Feature's entry, together with the entries of the
# Features that need it, keeps this passing.

set -uo pipefail
failures=0
check() {  # description  command...
    if "${@:2}" >/dev/null 2>&1 </dev/null; then echo "ok    $1"; else echo "FAIL  $1"; failures=$((failures + 1)); fi
}
has() { test -d "/usr/local/share/enchantments/$1"; }  # id

# A declared Feature without its marker would skip its checks instead of failing them.
while read -r id; do
    check "$id applied" has "$id"
done < <(grep -o 'ghcr\.io/jartan-llc/enchantments/[a-z0-9-]*' .devcontainer/devcontainer.json | sed 's|.*/||')
# Covers every Feature hook, grimoire's plugin install included. CI's gh isn't logged in,
# which gh-config records.
failed=
for f in "$HOME"/.cache/enchantments/*.failures*; do
    [ -e "$f" ] && [[ $f != */gh-config.failures* ]] && failed+=" ${f##*/}"
done
check "no Feature hook failed${failed:+:$failed}" test -z "$failed"
check "pre-commit hook wired" test -f "$(git rev-parse --git-path hooks)/pre-commit"
# Liza sets core.hooksPath in task worktrees; set here, make install skips pre-commit.
check "core.hooksPath unset" test -z "$(git config core.hooksPath)"
check "pnpm available" pnpm --version
# make install put the project into this Python; the package name comes from
# [tool.hatch.build.targets.wheel] packages.
if [ -f pyproject.toml ]; then
    pkg=$(python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"][0].rpartition("/")[2])')
    check "package $pkg imports" python -c "import $pkg"
    check "pytest collects the suite" pytest --collect-only -q
fi
if has claude-code; then
    check "Claude Code CLI runs" claude --version
    check "Claude config is in claude-data" test "$(readlink -f "$HOME/.claude")" = /mnt/enchantments/claude-data \
        -a "$(readlink -f "$HOME/.claude.json")" = /mnt/enchantments/claude-data/claude.json
fi
if has gh-config; then
    check "gh config is the gh-config mount" test "$(readlink -f "$HOME/.config/gh")" = /mnt/enchantments/gh-config
    check "gh-config mount owned by $(id -un)" test "$(stat -c %U /mnt/enchantments/gh-config)" = "$(id -un)"
fi
if has liza; then
    check "Liza activated and recorded" test -L CLAUDE.local.md -a -f "$(git rev-parse --git-path liza)/activation.json"
    check "ripgrep runs" "$HOME/.local/bin/rg" --version
else
    check "Liza not activated" test ! -e CLAUDE.local.md
fi
if has liza && has liza-toolchain; then
    # shellcheck disable=SC2016 # $t expands in the inner bash
    check "Liza toolchain installed" bash -c 'cd ~/.liza/bin && for t in ast-grep yq rtk stacklit scip-search \
        functional-clusters mdtoc bash-policy semble scip-python scip-typescript context7-mcp; do [ -x "$t" ] || exit 1; done'
    # mdq publishes no arm64 Linux build.
    [ "$(uname -m)" = x86_64 ] && check "mdq installed" test -x ~/.liza/bin/mdq
    has claude-code && check "context7 registered" claude mcp get context7
fi
if has codebase-memory-mcp && has claude-code; then
    check "codebase-memory-mcp registered" claude mcp get codebase-memory-mcp
fi
check "git status clean" test -z "$(git status --porcelain)"
exit $((failures > 0))
