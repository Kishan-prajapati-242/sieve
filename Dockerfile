# API image. Dev runs this with the source volume-mounted and --reload
# (see docker-compose.yml); the same image deploys to Render unchanged.
#
# Stage order is load-bearing: a target-less `docker build .` (which is what
# Render does) builds the LAST stage, so `runtime` sits at the bottom and the
# dev/test tooling can never leak into a deploy by default.

FROM python:3.12-slim AS base

# curl is needed by the runtime stage to fetch the embedding model.
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

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
COPY bench ./bench
CMD ["pytest", "-q"]

FROM base AS runtime

# The embedding model is fetched at BUILD time rather than committed. 130 MB
# of binary in git is permanent — every clone pays it forever, and history
# cannot be rewritten once it is shared. Skipped automatically when the model
# is bind-mounted (local compose), so this costs nothing in development.
# The layout must match OnnxEncoder, which opens <dir>/onnx/model.onnx. The
# first build flattened it to <dir>/model.onnx and every vector query 500'd
# with "Load model from /models/onnx/model.onnx failed" — so the layout is
# asserted here (`test -s`) rather than assumed.
ARG EMBED_MODEL_REPO=BAAI/bge-small-en-v1.5
ARG SKIP_MODEL_DOWNLOAD=0
RUN if [ "$SKIP_MODEL_DOWNLOAD" = "0" ]; then \
      set -eux; \
      mkdir -p /models/onnx; \
      base="https://huggingface.co/${EMBED_MODEL_REPO}/resolve/main"; \
      for f in tokenizer.json tokenizer_config.json special_tokens_map.json config.json; do \
        curl -fsSL "$base/$f" -o "/models/$f"; \
      done; \
      curl -fsSL "$base/onnx/model.onnx" -o /models/onnx/model.onnx; \
      test -s /models/onnx/model.onnx; \
      ls -la /models /models/onnx; \
    fi
ENV EMBED_MODEL_DIR=/models
