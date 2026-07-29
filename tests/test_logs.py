"""The JSON formatter's output is the contract later tooling parses."""

import json
import logging

from api.logs import JsonFormatter, request_id_var


def make_record(msg: str, **extra_fields: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="sieve.test", level=logging.INFO, pathname=__file__, lineno=1,
        msg=msg, args=(), exc_info=None,
    )  # fmt: skip
    if extra_fields:
        record.extra_fields = extra_fields
    return record


def test_lines_are_json_with_request_id_and_extras() -> None:
    token = request_id_var.set("req-7")
    try:
        line = JsonFormatter().format(make_record("search", took_ms=12.5, results=3))
    finally:
        request_id_var.reset(token)
    payload = json.loads(line)
    assert payload["msg"] == "search"
    assert payload["request_id"] == "req-7"
    assert payload["took_ms"] == 12.5
    assert payload["results"] == 3
    assert payload["level"] == "info"
    assert "ts" in payload
