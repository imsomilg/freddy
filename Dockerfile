FROM python:3.13-slim-trixie AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=0 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /uvx /bin/

FROM base AS deps
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

FROM base AS dev-deps
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project

FROM python:3.13-slim-trixie AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/src
RUN groupadd --system freddy && useradd --system --gid freddy --home /app freddy
WORKDIR /app
COPY --from=deps /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
USER freddy
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status == 200 else 1)"
CMD ["sh", "-c", "alembic upgrade head && exec python -m freddy"]

FROM app AS dev
USER root
COPY --from=dev-deps /app/.venv /app/.venv
COPY pyproject.toml ./
COPY tests ./tests
COPY scripts ./scripts
ENV RUFF_CACHE_DIR=/tmp/ruff \
    MYPY_CACHE_DIR=/tmp/mypy \
    PYTEST_ADDOPTS="-p no:cacheprovider"
USER freddy
CMD ["pytest"]

FROM app AS runtime
