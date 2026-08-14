# UI assembly plan — sources per effect, and the build order

Assembled from many sources, not adopted from one. Nothing here is built
until Kishan has seen the reorder and choreography proofs.

## Sources per category, and why each beat its alternatives

| category | lift from | why it beat the alternatives |
|---|---|---|
| **list reorder / enter / exit** | **Motion `layout` + `AnimatePresence`** | Not a preference — a hand-rolled FLIP **cannot animate exit**, because the element is unmounted before it can be moved. Verified against real data: hybrid→bm25 drops 3 rows, and the jargon query goes empty-state→8. Motion covers reorder, enter and exit in one model. |
| **card → detail expand** | pattern proven at **Aceternity layout-grid**; **implement with Motion's shared-layout (`layoutId`)** | Aceternity won as the only PROVABLE option, not the only good one. Frames show a grid card growing into a full panel, deltas 17.2 → 6.9 → 1.4 → 0.6. Vaul, Emil Kowalski's demos and Motion's own shared-layout examples yielded nothing **under my clicking** — three well-known sources failing the same way reads as a capture problem, not three empty libraries. Motion's shared-layout demos are the reference implementation for exactly this case and are the likelier implementation path once building starts. |
| **staggered entrance** | **Magic UI blur-fade** (pattern) | Verified staggered, not simultaneous: deltas rise then fall (5.5 → 14.5 → 15.0 → 4.0). Trivially reimplemented as a per-index delay, so the lift is the timing, not the code. |
| **state-change morph** (decision bar) | **SmoothUI Dynamic Island** (pattern) | Only verified container morph in the set. Motion's shared-layout is the general form and is already coming in for reorder, so no new dependency. |
| **text reveal** | **React Bits split-text**, restrained variant from **Cult UI** | Both verified. React Bits is the strongest and the only thing still moving on hover; Cult UI is the version that will not annoy on a second viewing. License note: React Bits is MIT + Commons Clause — fine, since we are not reselling it. |
| **number transitions** (count badges) | **Magic UI NumberTicker** | Weakly preferred: sustained small deltas consistent with digits rolling, where number-flow gave one paint. **Not confidently ranked** — both were near my noise floor. |
| **card hover** | **UNRESOLVED** | Aceternity's card-hover never observed in three attempts; 3d-card-effect showed 2.88 then flat. Waiting on Kishan's eyes. |
| **empty / loading** | **shadcn Skeleton** | Deliberately not searched further. A skeleton pulse is a ~2 s cycle my bursts cannot sample, and it is the most solved problem on the list. This is a lift, not a search. |

## Choreography — the open design question

Both variants are built and capturable in `.design-review/choreography-proof.html`:

* **Sequential** — departures clear (180 ms), then survivors move (420 ms), arrivals land at 55% of the move. Delta curve shows a **second peak** (6.86 at frame 6): the list visibly settles, then rearranges.
* **Simultaneous** — everything at once. One smooth decay, no second peak.

Kishan picks. My read from the stills is that simultaneous carries the
starred row upward as one continuous gesture and sequential reads as two
events, but that is exactly the judgment my 11-33 fps sampling should not
be trusted to make.

## Build order, with a checkpoint each

1. **Motion in and configured** — add `motion`, wrap the results list in
   `AnimatePresence`, no visual change yet.
   *Checkpoint:* 196 tests still green; search page pixel-identical.
   **EFFECT-INDEPENDENT** — can start regardless of any pick.
2. **Mode-toggle choreography** — `layout` on rows keyed by `r.id` (already
   the case), enter/exit per the chosen variant, plus the empty state, which
   is **on the demo path** rather than decoration.
   *Checkpoint:* all three demo queries switch modes without a jump; the
   de-identification row visibly rises to #1.
   *Depends on:* the choreography pick only.
3. **Search page restyle** — cards, hover, spacing, type.
   *Checkpoint:* `ResultCard.test.tsx` untouched and green — there are no
   class-name assertions anywhere in the frontend tests, so a restyle cannot
   break them.
   *Depends on:* the card-hover pick.
4. **View A, collections list** — list, create form, count badges, empty
   state.
   *Checkpoint:* create → appears in list → counts correct.
   **EFFECT-INDEPENDENT** except the number-badge treatment, which can land
   flat and be animated later.
5. **View B, collection detail** — decision bar with the morph, filter,
   export link, the papers list.
   *Checkpoint:* decide → re-decide → unscreen → export downloads a valid
   `.bib`.
   *Depends on:* the morph pick.
6. **"Add to…" on search results** — collection picker plus
   include/exclude/maybe, closing the loop.
   *Checkpoint:* screen from search → appears in View B. This is the step
   that makes Phase 3's "screening workflow usable end to end" true.
   **EFFECT-INDEPENDENT.**
7. **Card → detail expand** — the shared-layout transition into View B.
   *Checkpoint:* expand and back, no layout jump.
   *Depends on:* nothing outstanding. **DEFERRED ON COST — revisit after
   View B.** Not "least load-bearing": it is the only genuinely SPATIAL
   transition in the app — a collection card becoming the collection — and
   that is what makes software feel like software rather than pages. The
   label is deferral, not dismissal, and this line exists so it does not
   quietly become never.

**Steps 1, 4 and 6 can start before Kishan picks anything.** They are
structure, not motion.


## Built 2026-08-14 — all four visual calls applied

| decision | as built |
|---|---|
| choreography | `layout="position"` on rows, simultaneous. Stagger applies ONLY when the previous list was empty (`staggerFor`, 35 ms step, capped at 10 rows) — the jargon query's zero-to-eight is on the demo path. |
| card hover | CSS only: border, background, shadow, 2px lift. No JS listener anywhere in the list. |
| count badges | `CountBadge` keys on the value, so a change mounts a new node and ticks. No count-up on mount. |
| route transitions | crossfade + 6px y-offset, `AnimatePresence mode="wait"` so the outgoing route clears first. Card→detail expand still deferred on cost. |

**Bundle, measured by splitting each dependency into its own chunk:**

| chunk | raw | gzip |
|---|---|---|
| motion | 129.04 kB | **42.41 kB** |
| react-router-dom | 172.15 kB | 56.86 kB |
| @tanstack/react-query | 49.36 kB | 15.13 kB |
| app code | 17.70 kB | 5.06 kB |
| CSS | 19.33 kB | 4.38 kB |
| single-chunk total | 369.17 kB | **119.11 kB** |

**On LazyMotion / `motion/react-m`:** the headline saving does not apply
here. `LazyMotion` + the `domAnimation` feature set is the small bundle, but
it does **not** include layout animations — and `layout` / `layoutId` are
exactly what the mode-toggle re-rank and the decision-bar morph are built
on. The applicable feature set is `domMax`, which is most of the 42 kB. The
real benefit would be **deferral** rather than reduction: `LazyMotion` can
load features asynchronously so they are off the critical path for first
paint, which matters for a reviewer opening the demo on mobile. Worth doing
before the Phase 4 deploy; the size saving should be measured rather than
assumed, since I have not measured the `domMax` bundle in isolation.

**Corpus independence, confirmed:** nothing in `motion.ts`, `ResultCard`,
`SearchPage`, `CollectionsPage`, `CollectionPage`, `DecisionBar`,
`CountBadge` or `AddToCollection` references a paper id, a query string or a
corpus size. Rows are keyed on `r.id`, which is whatever the API returns.
**Only the DEMO QUERY SELECTION is corpus-dependent** — the animation code
survives the pull untouched.
