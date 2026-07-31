"""Process-wide query encoder for the API.

Lazy on purpose: the API serves bm25 without any model on disk (tests, CI,
fresh clones), and the first vector query pays the ONNX session load once
per process. embed_query() applies the bge instruction prefix ITSELF —
the prefix contract is enforced by construction at the only place query
text meets the encoder, so a route cannot bypass it; the tests stub the
encoder underneath this function and assert the prefix arrived.
"""

import os

from api.embed.onnx_encoder import OnnxEncoder
from api.embed.texts import query_text

_encoder: OnnxEncoder | None = None


def embed_query(raw_query: str) -> list[float]:
    """One prepared-and-prefixed query -> one L2-normalized vector."""
    global _encoder
    if _encoder is None:
        model_dir = os.environ.get("EMBED_MODEL_DIR")
        if not model_dir:
            raise RuntimeError(
                "EMBED_MODEL_DIR is not set: vector mode needs the bge model "
                "(see docs/progress.md for the fetch runbook)"
            )
        _encoder = OnnxEncoder(model_dir)
    return [float(x) for x in _encoder.encode([query_text(raw_query)])[0]]
