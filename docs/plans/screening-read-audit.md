# Screening read paths — what each role can see

Written 2026-08-17, before building the collaboration UI on top of them.

**Why this exists.** The CSV export leaked because a query was correct under
one-decision-per-paper and nobody re-checked it when that stopped being true.
Five new surfaces are about to consume these paths (members panel, conflicts
queue, reconciliation view, agreement display, and the search page's "Add
to…"), so every read that touches `screenings` is enumerated here with what it
returns per role.

**Two inputs, not one.** Every answer below depends on **role AND phase**.
Phase was added in 0017 and it is a blinding property, so a path checked
against role alone is a path that will be wrong the first time an owner
advances the collection.

| phase | what it does |
|---|---|
| `screening` | blinding on — the rule below applies |
| `review` | blinding lifted collection-wide; decisions visible, conflict queue unscoped |
| `closed` | as review, plus no writes to screenings or resolutions |

**The screening-phase rule:**

| stage | you may see |
|---|---|
| before you decide | nothing from anyone — no decisions, no notes, **no counts** |
| after you decide | others' decisions, never their notes |
| at reconciliation | notes in full (resolvers only) |

**Notes never open by phase.** `review` lifts the DECISION blind, not the
reasoning blind — reasoning still becomes visible only at reconciliation, and
only to someone who can resolve. Phase is about coordination; the note rule is
about anchoring, and they are different problems.

---

## Every path that reads `screenings`

| # | path | keyed on | screener, `screening` | screener, `review`/`closed` | resolver / owner |
|---|---|---|---|---|---|
| 1 | `GET /collections` — cards | `user_id` in `LIST_SQL` | own counts + team volume | unchanged | unchanged |
| 2 | `GET /collections/{id}` — detail | own rows always | own rows | own rows | own rows |
| 3 | `GET .../export.csv` | role → `see_all` | own rows + notes | own rows + notes | everything |
| 4 | `GET .../export.bib` | role → `see_all` | own includes | own includes | all includes |
| 5 | `GET .../papers/{pid}/screening` | **`is_blind(mode, phase)`** | nothing until decided | **all decisions, notes still sealed** | same |
| 6 | `GET .../conflicts` | **`sees_all_conflicts(role, phase)`** | papers they decided | **whole queue** | whole queue |
| 7 | `GET .../conflicts/{pid}` | `CAN_RESOLVE` | **404** | **404** | everything incl. notes |
| 8 | `GET .../agreement` | aggregate only | statistics, never individual calls | same | same |
| 9 | `GET .../members` | membership | roster + per-member volume | same | same |
| 10 | `GET .../phase` | membership | phase + history + reveal preview | same | same, plus `can_change` |
| 11 | `GET .../resolutions` | membership | rulings + derived `stale` flag | same | same |
| 12 | `GET /api/stats` — public | — | corpus facts only | — | — |
| 13 | dedup `merge_group` / `rollback` | not user-facing | — | — | — |

**Row 2 changed.** The detail view now returns the caller's own rows in every
phase and for every role. `PAPERS_SQL`'s per-(paper, screener) shape is right
for an export and wrong for a list of papers — with `see_all` an owner saw each
paper once per colleague. Others' calls arrive through row 5, which is where
the blinding rule already lives.

**Writes are phase-gated too**, which the table above does not cover: `closed`
returns 409 on both screening and resolution. Without it "finished" is a social
convention and a stray click months later edits a published record.

---

## Two leaks this audit found and closed

### Collection card counts aggregated every screener (paths 1)

`LIST_SQL` computed `included / excluded / maybe` across all rows in the
collection. If you had screened 5 papers and the card read "12 included", you
had just learned that colleagues included 7 papers you have not looked at.
That is the export bug one level up: correct when a paper had one decision,
silently wrong afterwards.

Now the decision counts are filtered to `s.user_id = %(user_id)s`. Two fields
were added that reveal **volume without judgement** — `team_screened` and
`screener_count` — because "how much work has been done" is not a leak and is
genuinely needed for coordination. Progress is not a judgement.

### The conflicts list was a signal in itself (path 6)

"This paper is contested" tells you two people already looked and could not
agree. That is arguably a *stronger* hint than seeing one person's decision,
because it says the paper is hard — and it was visible on papers the caller had
not screened.

A screener now sees conflicts only on papers they have already decided.
Resolvers see the whole queue, because adjudicating a queue you cannot see is
not a job. That asymmetry is accepted and is the reason `resolver` is separable
from `screener`: someone who only arbitrates has no judgement of their own to
bias, so the queue cannot anchor them.

### `/api/stats` published private review activity (path 10)

It is **unauthenticated** — the landing page reads the corpus size from it
before anyone signs in — and it returned global `screened` and `included`
counts across every user's private collections. On a small instance that is
worse than aggregate: with two users, one who knows their own total can
subtract it and get the other's exactly.

Removed rather than scoped, because a public endpoint has no caller to scope
to. It now reports corpus facts only, which is all the landing page ever used.

---

## Why counts are the dangerous category

Every leak found so far has been a **count or an aggregate**, not a decision
rendered directly:

* the export returned other people's note text — obvious once looked at
* the card counts revealed their verdicts in bulk — not obvious
* the public stats let one user derive another's totals by subtraction — not
  obvious at all

A decision leaking is visible in the response. An aggregate leaking looks like
a feature. The rule for anything built on top of these paths: **if a number
sums over rows belonging to more than one screener, it must not distinguish
between decisions.** Volume is safe; breakdown is not.

---

## Where the guard lives

`PAPERS_SQL` takes `see_all` and `user_id` and is the single query behind
paths 2, 3 and 4. `_fetch_papers` requires both as keyword-only arguments, so
a new export cannot inherit the permissive default by omission — there is no
default to inherit.

Paths 5–9 go through `api/collections/screening.py`, where the others-decisions
query **does not select the note column at all**. A projection that never
retrieves a field cannot leak it through a later refactor that forgets to
strip it.

**The thing to keep true:** no view builds its own SQL against `screenings`. If
a new surface needs a shape these do not provide, extend the module rather than
writing a query in the route — the route is where the next drift will happen.
