##############################################################################
# Build stage - resolve and install dependencies into a self-contained venv
##############################################################################
FROM python:3.13-slim AS build

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first: this layer is cached until pyproject.toml changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
RUN uv venv /opt/venv && \
    VIRTUAL_ENV=/opt/venv uv pip install --no-cache -r pyproject.toml

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini version.txt ./
RUN cp version.txt app/version.txt

##############################################################################
# Runtime stage - no build tools, no uv, non-root
##############################################################################
FROM python:3.13-slim

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN useradd --create-home --uid 10001 appuser

WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY --from=build --chown=appuser:appuser /app /app

USER appuser
EXPOSE 8080

# --factory: app.main has no module-level app instance on purpose (no Redis
# client as an import side effect).
CMD ["uvicorn", "app.main:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8080", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
