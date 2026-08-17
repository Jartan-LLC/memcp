ARG PYTHON_VERSION=3.12

# uv installs the package here, same as in CI and the devcontainer. It arrives
# as a build stage rather than the shorter `COPY --from=ghcr.io/astral-sh/uv`
# because Dependabot's Dockerfile parser only reads `FROM` lines — an inline
# COPY reference is a pin nobody bumps. Named `uv-bin` so it does not collide
# with the `uv` binary in a later RUN. Keep this version in step with
# ci/requirements.txt; Dependabot's docker and "/ci" entries move them
# separately.
FROM ghcr.io/astral-sh/uv:0.12.5 AS uv-bin

FROM python:${PYTHON_VERSION}-slim AS build

COPY --from=uv-bin /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml README.md ./
COPY memcp/ memcp/

# `--system` because the container is the isolation; no venv needed inside it.
# Nothing upgrades pip first any more — uv is not pip and does not bootstrap
# itself from the image's copy.
RUN uv pip install --system --no-cache .

FROM python:${PYTHON_VERSION}-slim

WORKDIR /app

RUN adduser --disabled-password --gecos "" memcp

COPY --from=build /usr/local/lib /usr/local/lib
COPY --from=build /usr/local/bin/memcp /usr/local/bin/memcp

USER memcp

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/health',timeout=5).status==200 else 1)" \
    || exit 1

ENTRYPOINT ["python", "-m", "memcp"]
