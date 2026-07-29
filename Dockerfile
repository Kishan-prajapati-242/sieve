# API image. Dev runs this with the source volume-mounted and --reload
# (see docker-compose.yml); the same image deploys to Render unchanged.

FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY api ./api
RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
