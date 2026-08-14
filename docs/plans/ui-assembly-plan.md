# UI assembly plan — sources per effect, and the build order

Assembled from many sources, not adopted from one. Nothing here is built
until Kishan has seen the reorder and choreography proofs.

## Sources per category, and why each beat its alternatives

| category | lift from | why it beat the alternatives |
|---|---|---|
| **list reorder / enter / exit** | **Motion `layout` + `AnimatePresence`** | Not a preference — a hand-rolled FLIP **cannot animate exit**, because the element is unmounted before it can be moved. Verified against real data: hybrid→bm25 drops 3 rows, and the jargon query goes empty-state→8. Motion covers reorder, enter and exit in one model. |
| **card → detail expand** | **Aceternity layout-grid** (pattern; shared-layout under the hood) | The only expand-into-detail I captured working: frames show a grid card growing into a full panel, deltas 17.2 → 6.9 → 1.4 → 0.6. Vaul, Emil Kowalski's demos and Motion's own shared-layout examples all failed to yield frames under my clicking, so this won on evidence, not on taste. |
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
   *Depends on:* nothing outstanding; deferred because it is the most
   expensive and the least load-bearing.

**Steps 1, 4 and 6 can start before Kishan picks anything.** They are
structure, not motion.
