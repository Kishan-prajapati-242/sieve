# Handover — visual instrument re-runs and the choreography defect

Written 2026-08-14. Everything here is blocked on nothing; start at the top.

**Do not start the PubMed pull.** Corpus stays at 183,167.

---

## 0. The instrument defect, in one paragraph

`page.screenshot()` is viewport-only unless `full_page=True`, and `full_page`
was never set in any of 19 capture scripts. Only 2 of 19 scrolled; none used
element-targeted captures. So every pixel-delta measurement in the design
funnel saw **the initial static viewport only**. The asymmetry matters: a
blind region can only produce FALSE NEGATIVES, so nothing was wrongly
accepted and the whole exposure is in what was **discarded**.

The default is now fixed in `.design-review/capture.py` — use `shoot()`,
`shoot_element()`, and `assert_in_frame()` rather than `page.screenshot()`.

## 1. Re-run list — these four only

The blind region could plausibly have hidden the thing being measured. All
were measured by `catalogue.py` / `shortlist.py`, neither of which scrolls.
Re-run with `full_page=True` **plus an explicit scroll to the card grid**.

| # | conclusion to retest | why it is at risk |
|---|---|---|
| 1 | Luxe — "measured zero twice" | card grids sit below the hero by convention |
| 2 | Kokonut — "measured zero twice" | same |
| 3 | Aceternity card-hover — 0.11 then flat x3 | the effect demonstrably exists |
| 4 | card-hover category — "unresolvable" | rests entirely on 1-3 |

### 1b. The universal negative — UNVERIFIED, and the exposed one

> **"Not one of the seven academic products explains its ranking."**

Called the most defensible line on the visual sheet. It is a **universal
negative built from static screenshots of initial viewports**. A "why this
result" affordance behind a click, a menu, or a hover would not appear. The
same false-negative logic that covers below-the-fold covers interaction-gated
UI, and with zero element-targeted captures the instrument never opened
anything.

**Treat as unverified. Do not repeat it in the README or an interview until
re-tested**, which means: for each of the seven products, click the result
row, open any overflow menu, and hover the score or metadata region.

**Survives, do not re-run:** Eldora (rejected on framing, not measurement);
the result-row-anatomy finding and its 37 screenshots (search products put
the results list near the top — the region the instrument DID see); the
density number (defined over a fixed viewport, so a viewport capture is the
correct instrument, not a limitation).

## 2. The choreography defect — re-derived severity

**It is a HOLE, not a collapse. The list never shrinks.** Measured at
t=279 ms in a 1000 px viewport:

| rank | title | state |
|---|---|---|
| 1, 2, 3 | survivors | opaque, settled |
| 4, 5 | arrivals | `opacity: 0` — not yet started |
| 6 | survivor | opaque, at the bottom edge |
| 8 | survivor | opaque, **below the fold** |

All five survivors are mounted at `opacity: 1.00` and correctly placed the
whole time. The reader sees three rows, a two-row gap, then a row at the
edge. Earlier passes described this as "the page collapses to 3 rows and
sits nearly empty", which was the instrument, not the product. **Do not tune
against the old description.**

## 3. Order of work, and why probe is first

### 3a. PROBE — the deferral question

> Does `AnimatePresence` with `layout` on sibling rows delay a child's
> `initial -> animate` variant transition until in-flight layout animations
> settle, **overriding the per-child `delay` passed via `custom`**?

Falsifiable in one run. Disable `layout` on survivors and measure entrance
start times for the 15 arrivals.

* **starts move to ~147 ms** (the computed gate) -> **the library defers.**
  Every schedule tuned so far — gated at 100% of the move, then overlapped
  at 35% — was arithmetic on a number framer-motion ignores, and the
  gate/overlap distinction never reached the DOM. The fix is then
  STRUCTURAL, not a tuning change.
* **starts stay at ~325-390 ms** -> hypothesis dead, look elsewhere.

Observed today: computed delays span 147-396 ms; at t=279 ms **all fifteen
arrivals** were still at `opacity: 0, translateY(8px)`, untouched; at
t=455 ms only ranks 19 and 20 were partially in (0.27, 0.52). The implied
start is ~325-390 ms, and the offset is close to the 420 ms move duration.

### 3b. NO-STAGGER — only after the probe

Try dropping the stagger for `bm25 -> hybrid` entirely. Rationale (Kishan):
the dump concern was judged on `bm25 -> vector`, which is 0 survivors and 20
arrivals into an empty list; here 5 survivors anchor the page so it cannot
read as a dump. No stagger means no hole, the count steps 5 -> 20 once, and
the invariant holds trivially.

**This is why the probe comes first: if `AnimatePresence` defers entrances
until layout settles, it overrides the custom delay, so removing the stagger
is a no-op and the fix has to be structural.** Testing no-stagger first would
produce a null result that looks like a refutation of the idea rather than of
the instrument.

### 3c. FRAMES — the same nine, full-height

Recapture with `full_page=True` so the new sheet compares directly with
`.design-review/rework/sheet.png`. State the display crop next to the sheet.

## 4. Keep regardless

`web/src/presentation.ts` and its six stepped tests survive any outcome
above. They encode the invariant frames found — a count and the rows it
describe must derive from one state — and they step the sequence rather than
sampling a running animation, so they need no timers and cannot go flaky.

## 5. Kishan's two open calls — neither blocks you

1. **The labeling session** (123 dedup pairs). His time, uncommitted; the
   precision number carries a labelled gap until it happens.
2. **The 7.1x framing.** Either the embed level travels with the number, or
   the claim moves to retrieval-only (24.1x, re-ran to 23.0x, overlapping
   CIs, no encoder term). See the resume-facing block in progress.md.
