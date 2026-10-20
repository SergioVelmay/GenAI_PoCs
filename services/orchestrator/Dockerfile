# Una imagen para los tres especialistas A2A; cada contenedor elige el suyo con el CMD:
#   docker build -f services/orchestrator/Dockerfile -t headache-agents:local .
#   docker run --rm -p 127.0.0.1:8202:8000 -v ./.secrets:/run/secrets/headache:ro \
#     -e A2A__PUBLIC_KEY_FILE=/run/secrets/headache/mcp-dev-key.pub headache-agents:local creative
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# El lock del monorepo necesita todos los miembros del workspace; .dockerignore deja fuera datos.
COPY . .
RUN uv sync --frozen --no-dev --no-editable --package orchestrator

FROM python:3.12-slim
RUN useradd --create-home --uid 10001 agent
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1
WORKDIR /app
USER agent
EXPOSE 8000
ENTRYPOINT ["python", "-m", "orchestrator.serve", "--host", "0.0.0.0", "--port", "8000"]
CMD ["knowledge"]
