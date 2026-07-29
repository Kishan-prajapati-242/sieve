# Decision Records

Each record is written in Kishan's words, at the moment the decision was made.
Format follows the template at the bottom of this file.

---

## DECISION-1: Backend language and framework

**Date:** 2026-07-28

**Decision:** Python + FastAPI for the entire backend.

**Alternatives considered:** Node/Express API with a Python sidecar for embeddings.

**Why rejected:** the embedding model (MiniLM via ONNX) is Python-native, so Node
forces two services, a network hop between them, two deploy targets, and two
dependency sets. The operational cost is real and the benefit is only that I
already know Express. Secondary reason: I have MERN experience already and want
backend range beyond it.

**What would change my mind:** if the frontend and backend needed to share
substantial validation or type logic, or if embeddings moved to a hosted API and
Python stopped being required in the request path.

---

## Template

```text
## DECISION-N: <short title>

**Date:** YYYY-MM-DD

**Decision:** <what was chosen>

**Alternatives considered:** <what else was on the table>

**Why rejected:** <the reasoning, in Kishan's words>

**What would change my mind:** <the conditions under which this gets revisited>
```
