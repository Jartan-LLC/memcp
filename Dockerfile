# Base pinned by digest, not just by tag: a tag is a moving target and a deployment
# that re-pulls should not silently change what it runs. The tag stays on the line so
# Dependabot can still see it and move both together.
ARG PYTHON_IMAGE=python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a

# Build stage, not an inline COPY --from: Dependabot only parses FROM lines.
FROM ghcr.io/astral-sh/uv:0.12.5 AS uv-bin

FROM ${PYTHON_IMAGE} AS build

COPY --from=uv-bin /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml README.md ./
COPY memcp/ memcp/

RUN uv pip install --system --no-cache .

FROM ${PYTHON_IMAGE}

WORKDIR /app

# /data exists and is owned by the runtime user before any volume is mounted over it:
# Docker copies that ownership onto a fresh named volume, which is what lets the
# non-root process write the sqlite backend's file.
RUN adduser --disabled-password --gecos "" memcp \
 && mkdir -p /data \
 && chown memcp:memcp /data

COPY --from=build /usr/local/lib /usr/local/lib
COPY --from=build /usr/local/bin/memcp /usr/local/bin/memcp

USER memcp

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/health',timeout=5).status==200 else 1)" \
    || exit 1

ENTRYPOINT ["python", "-m", "memcp"]
