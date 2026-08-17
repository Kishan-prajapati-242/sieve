# Collaborative screening — design proposal

**Status: APPROVED and being built (2026-08-17). Backend landed; UI outstanding.**

Kishan's two amendments and how they were resolved are recorded at the bottom
under "Amendments" — both changed the design rather than being absorbed into
it.

---

## What the domain actually requires

Systematic review has a settled method here, and it is not "let several people
edit the same list". Cochrane, PRISMA and every screening tool built for this
(Covidence, Rayyan, Abstrackr) share three properties:

1. **Two screeners work independently and blind.** Neither sees the other's
   call before making their own.
2. **Disagreement is the point, not a failure.** It gets counted, reported,
   and resolved on the record.
3. **Agreement is a published statistic.** Reviews report inter-rater
   agreement, usually Cohen's kappa, as evidence the screening was
   reproducible rather than one person's taste.

Sieve already speaks this language. The dedup work measured **Cohen's kappa
0.905** on hand-labelled pairs, with a documented protocol for what counted as
agreement. That is the same statistic on the same kind of judgement — so this
is a concept the project already owns, not one imported to look sophisticated.

That framing is what makes the design decisions non-arbitrary below.

---

## Four designs, and what is wrong with each

### A. Shared mutable collection ("Google Docs")

Everyone in the collection sees and edits the same screening rows.
`screenings` keeps its `(collection_id, paper_id)` key; membership replaces
the owner check. Smallest possible change.

**Against — and this is disqualifying.** It destroys the method three ways.
Seeing that a colleague marked a paper *include* anchors your own judgement,
which is the exact bias blind screening exists to prevent. Last-write-wins
silently discards a disagreement, which is the signal the method most wants to
capture. And it loses attribution entirely — "who decided this" has no answer,
so no agreement statistic can ever be computed.

It is the cheapest option and the only one that makes the product worse at its
job. **Reject.**

### B. Blind double screening with explicit reconciliation

Each member gets their own decision per paper. Nobody sees anyone else's until
they have decided (or until reconciliation opens). Conflicts are surfaced as a
first-class state; a resolver settles them; kappa is computed over the papers
both screened.

This is what Covidence and Rayyan do, and it is what a methodologist would
expect.

**Against.** Real cost. `screenings` becomes per-user, which is a migration
with a real backfill question. It adds UI states that do not exist today:
"screened by you, waiting on others", "conflict", "resolved". And for one
person working alone it is pure ceremony — a solo reviewer does not need a
reconciliation queue.

The last objection is answerable and it is what makes this viable: **a solo
collection is this design with one screener, where conflicts can never arise.**
The extra machinery costs a solo user nothing because it never activates. The
first two objections are real and are the price.

### C. Split workload (assignment)

Divide the papers among members; each screens their own slice. No overlap, no
conflicts, roughly N× faster.

**Against.** No overlap means **no agreement statistic is possible** — kappa
is undefined without papers two people both judged. It is triage, not
systematic review, and it cannot support the claim the project would want to
make about it.

But it is honest about what people do under deadline, and it is genuinely the
right mode for a first pass over 5,000 abstracts. **Reject as the default,
keep as an option** — and if it ships, it must not be allowed to report an
agreement number, because there is nothing to compute one from.

### D. Configurable per collection

Collection carries a `screening_mode`: `solo | split | double`. Each gets the
behaviour above.

**Against.** Three modes to build, test and document, and mode changes
mid-review are genuinely nasty — switching `split` to `double` retroactively
means most papers now have one screener where the mode expects two, and the
UI has to represent "partially double-screened", which is a state nobody
wants to design.

---

## Recommendation

**Build B's data model. Ship B and C as modes, with the mode fixed at
creation.**

The reasoning:

* **B's schema is a superset.** Per-user decisions support solo, split and
  double without three code paths — the modes differ in *who is asked to
  screen what*, not in how a decision is stored.
* **Solo costs nothing.** One screener, no conflicts, no reconciliation queue.
  The existing single-user experience is unchanged.
* **Mode is fixed at creation**, which removes the migration problem in D
  wholesale. Changing your mind means a new collection. That is a real
  limitation and worth stating rather than engineering around on speculation.
* **Split cannot report kappa** and the UI must say why, not hide the number.

### The specific calls

**Membership and roles — three, not five.**

| role | can |
|---|---|
| `owner` | everything, plus invite/remove members, resolve conflicts, delete |
| `screener` | screen assigned papers, see own decisions, export |
| `viewer` | read resolved decisions and export; cannot screen or see raw per-person calls |

One owner initially. Multiple owners is a natural extension and not needed to
be useful. `viewer` exists because "show my supervisor" is the most common
sharing request and it should not require handing over write access.

**Blind by default: yes.** A screener sees their own decision and nothing else
until they have recorded one. After they decide, others' calls become visible —
withholding them permanently helps nobody and makes reconciliation harder.

This is the decision with the largest UI consequence: the screening view can no
longer show a single decision state per paper. It shows *your* state, plus a
count of how many others have screened it, with their identities and calls
revealed only after you commit.

**Conflict is DERIVED, never stored.** A conflict is "two screeners, different
decisions, no resolution" — computable in SQL from rows that already exist.
Storing it creates a second source of truth that must be kept in sync with
every screening write, and it will drift. Derived means it cannot.

**Resolution is stored, and history is kept.** The resolver's call is another
row, flagged as the resolution, carrying who resolved it and why. Nothing is
overwritten — the record of "Ada said include, Grace said exclude, Ada resolved
to include because X" survives, which is exactly what a review needs to be
defensible.

**Kappa: surface it, with its guards.** The project already computes Cohen's
kappa and already knows the traps — it is undefined when one rater uses a
single category, and it is misleading below roughly 30 overlapping items. So it
is shown only once there are ≥2 screeners with ≥30 co-screened papers, with the
count beside it, and it is absent rather than estimated otherwise. That is the
same rule the bench harness already applies to unstable percentiles.

**Invitations: links, not email.** This is forced by the constraint we already
have — mail reaches exactly one address until the domain is verified. An invite
**link** carrying a single-use token works today, is shareable through any
channel, and is what Figma and Notion actually do regardless of email. Email
invitations become a convenience later, not a dependency now.

The token needs the same treatment as the session tokens already shipped:
256-bit, hashed at rest, single-use, expiring, and revocable by the owner.

---

## What it costs

**Schema (two migrations).**

```
collection_members (collection_id, user_id, role, invited_by, joined_at)
collection_invites (token_hash, collection_id, role, expires_at, used_at)
screenings         PK becomes (collection_id, paper_id, user_id)
                   + resolution columns (resolved_by, resolves)
collections        + screening_mode
```

The `screenings` primary-key change is the one that needs care. Existing rows
have no `user_id`; they get the collection owner's, which is true — the owner
is who made them. That backfill is exact rather than a guess, which is a
better position than the `collections.user_id` migration was in.

**API.** Roughly six new endpoints (members list/invite/accept/remove,
conflicts list, resolve) and a rewrite of the screening read path, which
currently assumes one decision per paper.

**UI.** The largest piece, and mostly new rather than modified: a members
panel, an invite flow, a conflicts queue, a resolution view showing both calls
side by side, and per-paper state that distinguishes yours from everyone's.

**Honest estimate: this is the biggest single feature in the project so far** —
comparable to the dedup cascade, larger than auth. It is not a weekend.

---

## What I would cut if it needs to be smaller

In order, cutting the least valuable first:

1. **`viewer` role** — sharing can be an export until it isn't.
2. **`split` mode** — `double` and `solo` cover the methodology; split is a
   speed optimisation.
3. **Kappa in the product** — the number can live in `bench/` where the
   project's other statistics already live, and be quoted rather than rendered.

What I would not cut: blind screening, derived conflicts, and preserved
resolution history. Those three *are* the feature. Without them this is design
A with extra tables, and design A makes the product worse.

---

## The question I cannot answer for him

**Is this a portfolio project or a tool with users?**

If it is a portfolio project defended in interviews, the *design* above is
worth more than the implementation — being able to explain why blind screening
changes the schema, why conflict is derived, and why kappa needs a minimum
sample is a stronger interview answer than a half-built members panel.

If it is meant to be used by two people on a real review, it needs building
properly and the estimate stands.

Those lead to different amounts of work and only Kishan knows which he wants.


---

# Amendments (Kishan, 2026-08-17)

## 1. N screeners, not two — and what that does to the statistic

The original proposal said "kappa" and would have been wrong. **Cohen's kappa
is defined for exactly two raters.** With N screeners, and with one paper
judged by three people while another was judged by two, there is no single
Cohen's kappa to compute, and averaging pairwise ones is not a statistic — it
is a number that resembles one.

Three candidates, and why the winner won:

| candidate | verdict |
|---|---|
| **Fleiss' kappa** | REJECTED. Generalises to N raters but requires the SAME number on every item. Meeting that means discarding every paper without exactly k screeners — and the retained subset is not random, because papers get extra screeners precisely when they are contentious. Throwing away data to fit a formula, and biasing what is left. |
| **Krippendorff's alpha** | ACCEPTED as the headline. Admits a variable number of raters per item and missing data by construction, which is exactly this situation. |
| **pairwise Cohen's kappa** | ACCEPTED as the detail. Not a single number, but the one a human can act on: "you and Grace agree at 0.84, you and Sam at 0.41" names where the problem is. |

Both ship, doing different jobs. Alpha answers *is this screening
reproducible*; the pairwise matrix answers *who needs to talk to whom*.

**Guards, the same refusal the bench harness applies to unstable percentiles:**
a pair needs ≥30 co-screened papers, alpha needs ≥50 multiply-screened papers,
and **both are undefined — not zero, not one — when every rater used a single
category.** That last case is the trap: two people who called everything
`include` agree perfectly and chance predicts exactly that, so kappa is 0/0.
Printing 1.0 would claim perfect reliability from raters who never
discriminated, which is the opposite of what happened. Detected and reported as
undefined, with the observed agreement alongside so the reader can see why.

Implemented in `api/collections/agreement.py`, with every expected value in the
tests derived by hand rather than copied from a library — the point of
implementing these is being able to defend the numbers.

## 2. When notes become visible

Kishan's observation is the one that decides it: **a colleague's reasoning is
more persuasive than their label.** Seeing "exclude" makes you wonder; seeing
"exclude — protocol paper, no results" hands you a conclusion that is hard to
argue with, whether or not it is right. So notes are protected MORE strictly
than decisions, in three stages:

| stage | you see |
|---|---|
| **before you decide** | nothing from anyone — no decisions, no notes, **not even a count**. A count is itself a signal ("three people already looked at this"), and blinding that leaks a hint is not blinding. |
| **after you decide** | others' DECISIONS, never their notes. Your call is committed and cannot be anchored retroactively, and knowing a disagreement exists is what makes reconciliation possible. Their reasoning stays sealed because there is nothing you should do with it yet. |
| **at reconciliation** | notes, in full. The goal has inverted — resolving REQUIRES understanding why the disagreement happened, and the notes are the most valuable thing on the screen. |

Enforced by projection, not by filtering: `OTHERS_DECISIONS_SQL` does not
select the note column at all, so a later refactor cannot forget to strip it.

## 3. One semantic change this forced elsewhere

Dedup merge conflict detection became **per (collection, screener)** rather
than per collection. One person calling two duplicates differently still blocks
a merge — collapsing those would silently pick one of their own judgements over
the other. But two people disagreeing about a paper is ordinary blind screening
and is the signal this whole feature exists to capture, so it must not block
anything. `test_two_screeners_disagreeing_does_not_block_a_merge` pins it.

## What landed

Migration 0016, `agreement.py`, `members.py`, `screening.py`, and the API:
members, invites, accept, remove, per-paper screening view, conflicts,
conflict detail, resolve, agreement. **265 backend tests green**, 30 of them new.

**Outstanding: the entire UI.** Members panel, invite flow, conflicts queue,
reconciliation view, agreement display, and the per-paper state that
distinguishes yours from everyone's.


---

# Amendment 4 — who resolves (2026-08-17)

**The problem.** Resolution was owner-only. In the two-person case Kishan
described — him and a co-author, both screening — the owner resolves every
conflict, which makes the tie-breaker whoever happened to create the
collection. Real systematic review uses a third party precisely because the two
disagreeing parties should not adjudicate themselves.

**Rejected: require a neutral resolver.** Methodologically correct and
unusable. In a two-person collection there is no third party, so requiring one
deadlocks the exact case that motivates the rule. A constraint that cannot be
satisfied is not a safeguard.

**Rejected: owner-resolves, unchanged.** Cheap and honest given preserved
history, but it leaves the asymmetry implicit. Someone reading the review later
would have to reconstruct from timestamps whether the adjudicator was party to
the dispute.

**Chosen: make the correct configuration possible, record it when it is not.**

1. A **`resolver` role**, optional, invitable. It can adjudicate but not
   administer — the shape a supervisor needs, and it means a three-person
   review CAN have a neutral arbitrator without also handing them the ability
   to remove people.
2. **`self_resolved`** on every resolution, computed at the moment of the
   ruling: was the adjudicator one of the people who disagreed? Recorded rather
   than prevented, and surfaced in the API response.

Computed at write time rather than derived on read, because the answer changes
if the resolver later edits their own screening — and what matters is whether
they were an interested party **when they ruled**.

**The limitation, stated plainly.** In a two-person collection the owner still
resolves their own disagreements. Nothing here prevents that, because nothing
can. What changes is that the record says so, so a reader can weigh it instead
of having to discover it.
