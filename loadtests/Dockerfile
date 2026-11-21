# Generador de carga del edge (10.G5): Locust con el mismo locustfile que en local, para un Cloud
# Run job (`infra/terraform/gcp/load_test.tf`) o cualquier contenedor. Versiones del lock:
#   docker build -f loadtests/Dockerfile -t genaipocs-load:local .
#   docker run --rm -e LOAD_PEAK=20 -v ./.secrets:/run/secrets/genaipocs:ro \
#     -e EDGE__DEV_PRIVATE_KEY_FILE=/run/secrets/genaipocs/mcp-dev-key.pem genaipocs-load:local \
#     --host http://host.docker.internal:8200 --headless --csv /tmp/protegido
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# El lock del monorepo necesita todos los miembros del workspace; .dockerignore deja fuera datos.
COPY . .
# El locustfile firma tokens con edge_api; Locust es un grupo del proyecto raíz (no lo instala la
# CI): se exporta con las versiones del lock y se instala en el mismo entorno.
RUN uv sync --frozen --no-dev --no-editable --package edge-api \
    && uv export --frozen --only-group load --no-hashes --no-emit-workspace -o /tmp/load.txt \
    && uv pip install --python /app/.venv/bin/python -r /tmp/load.txt

FROM python:3.12-slim
RUN useradd --create-home --uid 10001 load
COPY --from=build /app/.venv /app/.venv
COPY loadtests /app/loadtests
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1
WORKDIR /app
USER load
# Todos los núcleos de la máquina: un maestro y un worker por CPU en el mismo contenedor (en Cloud
# Run no hay red entre instancias para el modo distribuido de Locust).
ENTRYPOINT ["locust", "-f", "loadtests/locustfile.py", "--processes", "-1"]
