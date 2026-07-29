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
