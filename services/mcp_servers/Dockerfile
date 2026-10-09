# Una imagen para los tres servidores MCP; cada contenedor elige el suyo con el CMD:
#   docker build -f services/mcp_servers/Dockerfile -t headache-mcp:local .
#   docker run --rm -p 127.0.0.1:8101:8101 headache-mcp:local catalog
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# El lock del monorepo necesita todos los miembros del workspace; .dockerignore deja fuera datos.
COPY . .
RUN uv sync --frozen --no-dev --no-editable --package mcp-servers

FROM python:3.12-slim
RUN useradd --create-home --uid 10001 mcp
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    MCP__HOST=0.0.0.0
USER mcp
EXPOSE 8101 8102 8103
ENTRYPOINT ["python", "-m", "headache_mcp"]
CMD ["catalog"]
