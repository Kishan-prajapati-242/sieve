# Screening read paths — what each role can see

Written 2026-08-17, before building the collaboration UI on top of them.

**Why this exists.** The CSV export leaked because a query was correct under
one-decision-per-paper and nobody re-checked it when that stopped being true.
Five new surfaces are about to consume these paths (members panel, conflicts
queue, reconciliation view, agreement display, and the search page's "Add
to…"), so every read that touches `screenings` is enumerated here with what it
returns per role.

**The rule being enforced**, from the design record:

| stage | you may see |
|---|---|
| before you decide | nothing from anyone — no decisions, no notes, **no counts** |
| after you decide | others' decisions, never their notes |
| at reconciliation | notes in full (owners only) |

---

## Every path that reads `screenings`

| # | path | scoped by | screener sees | owner sees | viewer sees |
|---|---|---|---|---|---|
| 1 | `GET /collections` — the cards | `%(user_id)s` filter in `LIST_SQL` | own counts + team volume | same | same |
| 2 | `GET /collections/{id}` — detail | `see_all` in `PAPERS_SQL` | own rows only | all rows | own (none) + resolutions |
| 3 | `GET .../export.csv` | `see_all` in `PAPERS_SQL` | own rows + notes | everything | resolutions |
| 4 | `GET .../export.bib` | `see_all` in `PAPERS_SQL` | own includes | all includes | resolutions |
| 5 | `GET .../papers/{pid}/screening` | `paper_view(blind=…)` | own; others' decisions **after deciding** | same | same |
| 6 | `GET .../conflicts` | derived query | which papers are contested | same | same |
| 7 | `GET .../conflicts/{pid}` | `CAN_RESOLVE` | **404** | everything incl. notes | **404** |
| 8 | `GET .../agreement` | aggregate only | statistics, no individual calls | same | same |
| 9 | `GET .../members` | membership | roster + per-member volume | same | same |
| 10 | `GET /api/stats` — public | — | corpus facts only | — | — |
| 11 | dedup `merge_group` / `rollback` | not user-facing | — | — | — |

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
