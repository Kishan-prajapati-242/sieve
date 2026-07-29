"""Test defaults.

Tests run against the real compose Postgres, no mocks — the point of this
project is the database behavior. DATABASE_URL from the environment wins
(CI sets it); the default matches .env.example for local runs.
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql://sieve:sieve@localhost:5432/sieve")
