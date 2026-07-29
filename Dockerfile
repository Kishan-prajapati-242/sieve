# API image. Dev runs this with the source volume-mounted and --reload
# (see docker-compose.yml); the same image deploys to Render unchanged.

FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY api ./api
RUN pip install --no-cache-dir .

EXPOSE 8000

# Render injects PORT (and health-checks it); local keeps 8000. A JSON-array
# CMD does no shell expansion, hence the sh -c.
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
