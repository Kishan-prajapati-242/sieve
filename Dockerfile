# API image. Dev runs this with the source volume-mounted and --reload
# (see docker-compose.yml); the same image deploys to Render unchanged.
#
# Stage order is load-bearing: a target-less `docker build .` (which is what
# Render does) builds the LAST stage, so `runtime` sits at the bottom and the
# dev/test tooling can never leak into a deploy by default.

FROM python:3.12-slim AS base

WORKDIR /app

COPY pyproject.toml ./
COPY api ./api
RUN pip install --no-cache-dir .

EXPOSE 8000

# Render injects PORT (and health-checks it); local keeps 8000. A JSON-array
# CMD does no shell expansion, hence the sh -c.
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

# The compose `test` service builds this target: same image as production
# plus pytest/ruff/mypy, so `make test` verifies the suite without a host
# Python (DECISION-1d) — and without trusting anyone's reported counts.
FROM base AS dev
RUN pip install --no-cache-dir ".[dev]"
COPY tests ./tests
CMD ["pytest", "-q"]

FROM base AS runtime
