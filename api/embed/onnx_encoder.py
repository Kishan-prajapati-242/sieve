"""bge-small-en-v1.5 on ONNX Runtime CPU (DECISION-2b), fp32 (DECISION-2d).

fp32 deliberately: int8 halves wall clock but cosine parity per document
does not prove ranking is preserved — int8 is deferred to Phase 4, where
it gets measured as Recall@10 against this index as ground truth.

Batches are length-sorted internally (output order restored): padding cost
is set by the longest document in a batch, and sorting was measured at
~2x throughput on the real corpus (docs/progress.md 2026-07-29). Sorting
changes vectors only at float-noise level (measured max 2.4e-07), which
matters because resumability promises byte-identical rows are never
rewritten — and they never are: rows are written once, batching noise
cannot touch a committed row.

CLS pooling per the model's own 1_Pooling/config.json (NOT mean pooling —
bge differs from MiniLM here), then L2 normalization.
"""

from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

EMBED_DIM = 384
MAX_TOKENS = 512  # DECISION-2b: bge is trained at 512; 94.4% of corpus fits untruncated


class OnnxEncoder:
    def __init__(self, model_dir: str | Path, batch_size: int = 32) -> None:
        model_dir = Path(model_dir)
        self.tokenizer: Tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=MAX_TOKENS)
        self.tokenizer.no_padding()  # padded per batch below, to the batch max
        self.session = ort.InferenceSession(
            str(model_dir / "onnx" / "model.onnx"), providers=["CPUExecutionProvider"]
        )
        self._input_names = {i.name for i in self.session.get_inputs()}
        self.batch_size = batch_size

    def encode(self, texts: list[str]) -> np.ndarray:
        """L2-normalized fp32 vectors, one row per input, input order."""
        encodings = self.tokenizer.encode_batch(texts)
        order = sorted(range(len(texts)), key=lambda i: len(encodings[i].ids))
        out = np.zeros((len(texts), EMBED_DIM), dtype=np.float32)
        for start in range(0, len(order), self.batch_size):
            idx = order[start : start + self.batch_size]
            out[idx] = self._run_batch([encodings[i] for i in idx])
        return out

    def _run_batch(self, batch: list[object]) -> np.ndarray:
        width = max(len(e.ids) for e in batch)  # type: ignore[attr-defined]
        ids = np.zeros((len(batch), width), dtype=np.int64)
        mask = np.zeros((len(batch), width), dtype=np.int64)
        for i, enc in enumerate(batch):
            enc_ids = enc.ids  # type: ignore[attr-defined]
            ids[i, : len(enc_ids)] = enc_ids
            mask[i, : len(enc_ids)] = 1
        feeds = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._input_names:
            feeds["token_type_ids"] = np.zeros((len(batch), width), dtype=np.int64)
        (hidden,) = self.session.run(["last_hidden_state"], feeds)
        cls = hidden[:, 0, :]
        result: np.ndarray = cls / np.linalg.norm(cls, axis=1, keepdims=True)
        return result.astype(np.float32)
