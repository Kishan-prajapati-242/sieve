# Everything runs in Docker (DECISION-1d: zero host dependencies). These
# exist so test results can be verified independently — same containers,
# same commands, no trusting a reported count.

.PHONY: test test-web lint

# Python suite against the real compose Postgres. Build is cached; it only
# actually rebuilds when pyproject.toml changes.
test:
	docker compose build test
	docker compose run --rm test

# Frontend component suite. --no-deps: vitest stubs fetch, so it needs
# neither the API nor the database.
test-web:
	docker compose run --rm --no-deps web sh -c "npm install && npm test"

# Exactly what CI runs, in the same order.
lint:
	docker compose build test
	docker compose run --rm --no-deps test sh -c "ruff check . && ruff format --check . && mypy api tests"

# Dedup hand-labeling (Core 2). Sample once, then label in as many sittings
# as you like — every answer is written to disk immediately.
.PHONY: dedup-sample label dedup-precision

dedup-sample:
	docker compose run --rm --no-deps -v ./bench:/app/bench -v ./api:/app/api \
	  -e DATABASE_URL=postgresql://sieve:sieve@postgres:5432/sieve \
	  test python -m bench.dedup_sample

# Interactive: needs a TTY for the prompt, which `docker compose run` gives.
label:
	docker compose run --rm --no-deps -v ./bench:/app/bench -v ./api:/app/api \
	  -e DATABASE_URL=postgresql://sieve:sieve@postgres:5432/sieve \
	  test python -m bench.dedup_label

dedup-precision:
	docker compose run --rm --no-deps -v ./bench:/app/bench -v ./api:/app/api \
	  -e DATABASE_URL=postgresql://sieve:sieve@postgres:5432/sieve \
	  test python -m bench.dedup_precision
