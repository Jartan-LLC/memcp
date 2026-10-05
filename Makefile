# Task runner for the local dev loop. Run `make` or `make help` to list targets.
.PHONY: help install lint fix typecheck test test-integration check all

# Every target uses one Python environment, chosen here: this
# checkout's .venv, else the active one, else, in the main checkout only, the system
# Python under UV_SYSTEM_PYTHON. Each uv install names it: uv skips .venv under
# UV_SYSTEM_PYTHON and, with both variables set, picks the system Python over VIRTUAL_ENV.
CHECKOUT_GIT_DIR := $(shell git rev-parse --absolute-git-dir 2>/dev/null)
ifneq ($(wildcard $(CURDIR)/.venv),)
  PYENV := $(CURDIR)/.venv
else ifneq ($(VIRTUAL_ENV),)
  PYENV := $(VIRTUAL_ENV)
endif
ifdef PYENV
  UV_TARGET := --python $(PYENV)
  export VIRTUAL_ENV := $(PYENV)
  export PATH := $(PYENV)/bin:$(PATH)
  unexport UV_SYSTEM_PYTHON
else ifneq ($(filter 1 true,$(UV_SYSTEM_PYTHON)),)
  # A linked worktree must not replace the main checkout's install in the system Python.
  ifeq ($(CHECKOUT_GIT_DIR),$(shell git rev-parse --path-format=absolute --git-common-dir 2>/dev/null))
    UV_TARGET := --system
  endif
endif
NO_ENV := No Python environment for this checkout: create one with `uv venv` or activate one (CONTRIBUTING.md, Setup)
UV_INSTALL = uv pip install $(or $(UV_TARGET),$(error $(NO_ENV)))

# Manifests: git-tracked only, so task worktrees and scratch copies never leak in.
MANIFEST_EXCLUDES := $(foreach d,.worktrees .adversarial .liza node_modules .venv venv .tox,':(exclude,glob)**/$(d)/**')
manifests = $(if $(CHECKOUT_GIT_DIR),$(shell git ls-files -- ':(glob)**/$(1)' $(MANIFEST_EXCLUDES)),$(wildcard $(1)))
PY_PROJECTS = $(patsubst %/pyproject.toml,./%,$(patsubst pyproject.toml,.,$(call manifests,pyproject.toml)))
NODE_DIRS = $(patsubst %/,%,$(dir $(call manifests,package.json)))

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-12s %s\n", $$1, $$2}'

# pre-commit install refuses a checkout without git or with core.hooksPath set, which Liza
# sets in task worktrees.
install:  ## Install every tracked Python and Node manifest, then wire the pre-commit hook
	$(UV_INSTALL) $(foreach p,$(PY_PROJECTS),-e '$(p)[dev]') $(foreach r,$(call manifests,requirements.txt),-r $(r))
	$(if $(NODE_DIRS),@command -v pnpm >/dev/null || { echo "pnpm not found; it installs: $(NODE_DIRS)" >&2; exit 1; })
	$(if $(NODE_DIRS),$(foreach d,$(NODE_DIRS),CI=true pnpm --dir $(d) install &&) true)
	@if ! git rev-parse --git-dir >/dev/null 2>&1; then :; \
	elif [ -n "$$(git config core.hooksPath)" ]; then echo "core.hooksPath is set; skipping pre-commit install"; \
	else pre-commit install; fi

lint:  ## Lint all files via pre-commit (ruff, codespell, shellcheck, markdownlint, lychee, actionlint, zizmor, hygiene)
	pre-commit run --all-files

# A hook run that rewrites files exits 1; the rerun passes unless a finding or error remains.
fix:  ## Apply ruff's safe fixes and formatting via its pre-commit hooks (git-tracked files: `git add` new ones first)
	pre-commit run ruff-check --all-files || pre-commit run ruff-check --all-files
	pre-commit run ruff-format --all-files || pre-commit run ruff-format --all-files

typecheck:  ## Static type check (pyright)
	pyright

test:  ## Run the unit suite (matches CI: excludes integration-marked tests)
	pytest -m "not integration"

test-integration:  ## Run only integration-marked tests
	pytest -m integration

check:  ## Run CI's lint, typecheck, test, build and audit checks
	$(MAKE) lint typecheck test
	uv build
	python -m twine check dist/*
# Advisory, as in CI: known vulnerabilities are reported without failing the gate.
	-pip-audit

all: check  ## Alias for `check`
