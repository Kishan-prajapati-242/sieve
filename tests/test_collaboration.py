"""Collaborative screening: membership, blinding, conflicts, concurrency.

The properties that matter here are the ones that make the feature worth
having. A members table that lets two people edit one row would pass a naive
test suite and destroy the methodology, so these pin the METHOD: independence
before a decision, notes sealed until reconciliation, conflicts derived rather
than stored, and history preserved through resolution.
"""

from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from api.db.migrate import migrate
from api.db.pool import close_pool
from api.main import app

PW = "a-long-test-password"


def _register(c: TestClient, dsn: str, email: str) -> int:
    from api.auth import codes

    c.post("/api/auth/signup", json={"email": email, "password": PW}).raise_for_status()
    with psycopg.connect(dsn, autocommit=True) as conn:
        row = conn.execute("SELECT id FROM users WHERE lower(email)=lower(%s)", (email,)).fetchone()
        assert row is not None
        code = codes.issue(conn, int(row[0]))
    c.post("/api/auth/verify", json={"code": code}).raise_for_status()
    return int(row[0])


@pytest.fixture
def dsn(scratch_db: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    migrate(scratch_db)
    close_pool()
    monkeypatch.setenv("DATABASE_URL", scratch_db)
    yield scratch_db
    close_pool()


def client_for(dsn: str, email: str) -> TestClient:
    c = TestClient(app)
    c.__enter__()
    _register(c, dsn, email)
    return c


def seed(dsn: str, n: int) -> list[int]:
    ids = []
    with psycopg.connect(dsn, autocommit=True) as conn:
        for i in range(n):
            row = conn.execute(
                "INSERT INTO papers (title, title_norm, year) VALUES (%s, %s, 2020) RETURNING id",
                (f"Paper {i}", f"paper {i}"),
            ).fetchone()
            assert row is not None
            ids.append(int(row[0]))
    return ids


class TestMembershipAndInvites:
    def test_invite_link_grants_access_and_is_single_use(self, dsn: str) -> None:
        ada = client_for(dsn, "ada@example.com")
        cid = ada.post(
            "/api/collections", json={"name": "Review", "screening_mode": "blind"}
        ).json()["id"]
        token = ada.post(f"/api/collections/{cid}/invites", json={"role": "screener"}).json()[
            "token"
        ]

        grace = client_for(dsn, "grace@example.com")
        assert grace.get(f"/api/collections/{cid}").status_code == 404  # not yet a member
        assert grace.post(f"/api/collections/invites/{token}/accept").status_code == 200
        assert grace.get(f"/api/collections/{cid}").status_code == 200

        # Spent. A link that keeps working is a link that leaks.
        sam = client_for(dsn, "sam@example.com")
        assert sam.post(f"/api/collections/invites/{token}/accept").status_code == 400
        assert sam.get(f"/api/collections/{cid}").status_code == 404

    def test_invite_token_is_hashed_at_rest(self, dsn: str) -> None:
        ada = client_for(dsn, "ada@example.com")
        cid = ada.post("/api/collections", json={"name": "R"}).json()["id"]
        token = ada.post(f"/api/collections/{cid}/invites", json={}).json()["token"]
        with psycopg.connect(dsn) as conn:
            row = conn.execute("SELECT token_hash FROM collection_invites").fetchone()
        # An unused invite IS a credential; a database dump must not contain it.
        assert row is not None and token not in row[0] and len(row[0]) == 64

    def test_only_owners_invite(self, dsn: str) -> None:
        ada = client_for(dsn, "ada@example.com")
        cid = ada.post("/api/collections", json={"name": "R"}).json()["id"]
        token = ada.post(f"/api/collections/{cid}/invites", json={"role": "screener"}).json()[
            "token"
        ]
        grace = client_for(dsn, "grace@example.com")
        grace.post(f"/api/collections/invites/{token}/accept")
        assert grace.post(f"/api/collections/{cid}/invites", json={}).status_code == 404

    def test_the_last_owner_cannot_be_removed(self, dsn: str) -> None:
        ada = client_for(dsn, "ada@example.com")
        cid = ada.post("/api/collections", json={"name": "R"}).json()["id"]
        me = ada.get("/api/auth/me").json()["id"]
        resp = ada.delete(f"/api/collections/{cid}/members/{me}")
        # An ownerless collection can be administered by nobody and deleted by
        # nobody — a permanent orphan.
        assert resp.status_code == 400
        assert "at least one owner" in resp.json()["detail"]

    def test_viewers_cannot_screen(self, dsn: str) -> None:
        ada = client_for(dsn, "ada@example.com")
        pid = seed(dsn, 1)[0]
        cid = ada.post("/api/collections", json={"name": "R"}).json()["id"]
        token = ada.post(f"/api/collections/{cid}/invites", json={"role": "viewer"}).json()[
            "token"
        ]
        bob = client_for(dsn, "bob@example.com")
        bob.post(f"/api/collections/invites/{token}/accept")
        assert bob.get(f"/api/collections/{cid}").status_code == 200
        assert (
            bob.put(
                f"/api/collections/{cid}/screenings/{pid}", json={"decision": "include"}
            ).status_code
            == 404
        )


class TestBlinding:
    """The methodological core. If this leaks, the feature is worse than useless."""

    def _two_screeners(self, dsn: str):  # type: ignore[no-untyped-def]
        ada = client_for(dsn, "ada@example.com")
        cid = ada.post(
            "/api/collections", json={"name": "Blind review", "screening_mode": "blind"}
        ).json()["id"]
        token = ada.post(f"/api/collections/{cid}/invites", json={"role": "screener"}).json()[
            "token"
        ]
        grace = client_for(dsn, "grace@example.com")
        grace.post(f"/api/collections/invites/{token}/accept")
        return ada, grace, cid

    def test_undecided_screener_sees_nothing_not_even_a_count(self, dsn: str) -> None:
        pid = seed(dsn, 1)[0]
        ada, grace, cid = self._two_screeners(dsn)
        ada.put(
            f"/api/collections/{cid}/screenings/{pid}",
            json={"decision": "include", "note": "clearly a trial"},
        )
        view = grace.get(f"/api/collections/{cid}/papers/{pid}/screening").json()
        # A count is itself a signal — "three people already looked at this"
        # tells you something. Blinding that leaks a hint is not blinding.
        assert view["blinded"] is True
        assert view["mine"] is None
        assert view["others"] == []

    def test_after_deciding_you_see_decisions_but_never_notes(self, dsn: str) -> None:
        pid = seed(dsn, 1)[0]
        ada, grace, cid = self._two_screeners(dsn)
        ada.put(
            f"/api/collections/{cid}/screenings/{pid}",
            json={"decision": "include", "note": "SECRET REASONING"},
        )
        grace.put(f"/api/collections/{cid}/screenings/{pid}", json={"decision": "exclude"})

        view = grace.get(f"/api/collections/{cid}/papers/{pid}/screening").json()
        assert view["blinded"] is False
        assert [o["decision"] for o in view["others"]] == ["include"]
        # Reasoning is MORE persuasive than a label, so it stays sealed longer.
        assert view["notes_visible"] is False
        assert "SECRET REASONING" not in str(view)
        assert all("note" not in o for o in view["others"])

    def test_notes_open_at_reconciliation_and_only_there(self, dsn: str) -> None:
        pid = seed(dsn, 1)[0]
        ada, grace, cid = self._two_screeners(dsn)
        ada.put(
            f"/api/collections/{cid}/screenings/{pid}",
            json={"decision": "include", "note": "randomised, meets criteria"},
        )
        grace.put(
            f"/api/collections/{cid}/screenings/{pid}",
            json={"decision": "exclude", "note": "protocol only, no results"},
        )
        detail = ada.get(f"/api/collections/{cid}/conflicts/{pid}").json()
        assert detail["notes_visible"] is True
        notes = {o["note"] for o in detail["others"]}
        # Resolving REQUIRES understanding why — the reasoning is the point here.
        assert "protocol only, no results" in notes

        # And a non-resolver still cannot open it.
        assert grace.get(f"/api/collections/{cid}/conflicts/{pid}").status_code == 404

    def test_solo_mode_does_not_blind(self, dsn: str) -> None:
        pid = seed(dsn, 1)[0]
        ada = client_for(dsn, "ada@example.com")
        cid = ada.post(
            "/api/collections", json={"name": "Mine", "screening_mode": "solo"}
        ).json()["id"]
        view = ada.get(f"/api/collections/{cid}/papers/{pid}/screening").json()
        assert view["blinded"] is False  # nobody to be blinded from


class TestConflictsAndResolution:
    def _disagreement(self, dsn: str):  # type: ignore[no-untyped-def]
        pids = seed(dsn, 2)
        ada = client_for(dsn, "ada@example.com")
        cid = ada.post(
            "/api/collections", json={"name": "R", "screening_mode": "blind"}
        ).json()["id"]
        token = ada.post(f"/api/collections/{cid}/invites", json={"role": "screener"}).json()[
            "token"
        ]
        grace = client_for(dsn, "grace@example.com")
        grace.post(f"/api/collections/invites/{token}/accept")
        # Disagree on the first, agree on the second.
        ada.put(f"/api/collections/{cid}/screenings/{pids[0]}", json={"decision": "include"})
        grace.put(f"/api/collections/{cid}/screenings/{pids[0]}", json={"decision": "exclude"})
        ada.put(f"/api/collections/{cid}/screenings/{pids[1]}", json={"decision": "include"})
        grace.put(f"/api/collections/{cid}/screenings/{pids[1]}", json={"decision": "include"})
        return ada, grace, cid, pids

    def test_only_disagreements_are_conflicts(self, dsn: str) -> None:
        ada, _grace, cid, pids = self._disagreement(dsn)
        conflicts = ada.get(f"/api/collections/{cid}/conflicts").json()["conflicts"]
        assert [c["paper_id"] for c in conflicts] == [pids[0]]
        assert conflicts[0]["distinct_decisions"] == 2

    def test_resolving_clears_the_conflict_and_keeps_both_calls(self, dsn: str) -> None:
        ada, _grace, cid, pids = self._disagreement(dsn)
        ada.put(
            f"/api/collections/{cid}/conflicts/{pids[0]}",
            json={"decision": "include", "note": "trial, Grace read the protocol paper"},
        )
        assert ada.get(f"/api/collections/{cid}/conflicts").json()["conflicts"] == []

        # NOTHING is overwritten. "Ada said include, Grace said exclude, Ada
        # resolved to include" is what makes a review defensible later.
        detail = ada.get(f"/api/collections/{cid}/conflicts/{pids[0]}").json()
        assert {o["decision"] for o in detail["others"]} == {"include", "exclude"}

    def test_screeners_cannot_resolve(self, dsn: str) -> None:
        _ada, grace, cid, pids = self._disagreement(dsn)
        assert (
            grace.put(
                f"/api/collections/{cid}/conflicts/{pids[0]}", json={"decision": "include"}
            ).status_code
            == 404
        )


class TestConcurrency:
    def test_two_screeners_on_one_paper_do_not_collide(self, dsn: str) -> None:
        """Safe by SCHEMA, not by locking: the primary key includes user_id, so
        simultaneous writes are simply different rows."""
        pid = seed(dsn, 1)[0]
        ada = client_for(dsn, "ada@example.com")
        cid = ada.post(
            "/api/collections", json={"name": "R", "screening_mode": "blind"}
        ).json()["id"]
        token = ada.post(f"/api/collections/{cid}/invites", json={"role": "screener"}).json()[
            "token"
        ]
        grace = client_for(dsn, "grace@example.com")
        grace.post(f"/api/collections/invites/{token}/accept")

        ada.put(f"/api/collections/{cid}/screenings/{pid}", json={"decision": "include"})
        grace.put(f"/api/collections/{cid}/screenings/{pid}", json={"decision": "exclude"})
        with psycopg.connect(dsn) as conn:
            n = conn.execute(
                "SELECT count(*) FROM screenings WHERE collection_id=%s AND paper_id=%s",
                (cid, pid),
            ).fetchone()
        assert n is not None and n[0] == 2  # both survived; neither overwrote

    def test_changing_your_own_mind_updates_in_place(self, dsn: str) -> None:
        pid = seed(dsn, 1)[0]
        ada = client_for(dsn, "ada@example.com")
        cid = ada.post("/api/collections", json={"name": "R"}).json()["id"]
        ada.put(f"/api/collections/{cid}/screenings/{pid}", json={"decision": "include"})
        ada.put(f"/api/collections/{cid}/screenings/{pid}", json={"decision": "exclude"})
        with psycopg.connect(dsn) as conn:
            rows = conn.execute(
                "SELECT decision FROM screenings WHERE collection_id=%s AND paper_id=%s",
                (cid, pid),
            ).fetchall()
        assert [r[0] for r in rows] == ["exclude"]

    def test_unscreening_removes_only_your_own_row(self, dsn: str) -> None:
        pid = seed(dsn, 1)[0]
        ada = client_for(dsn, "ada@example.com")
        cid = ada.post(
            "/api/collections", json={"name": "R", "screening_mode": "blind"}
        ).json()["id"]
        token = ada.post(f"/api/collections/{cid}/invites", json={"role": "screener"}).json()[
            "token"
        ]
        grace = client_for(dsn, "grace@example.com")
        grace.post(f"/api/collections/invites/{token}/accept")
        ada.put(f"/api/collections/{cid}/screenings/{pid}", json={"decision": "include"})
        grace.put(f"/api/collections/{cid}/screenings/{pid}", json={"decision": "exclude"})

        grace.delete(f"/api/collections/{cid}/screenings/{pid}")
        with psycopg.connect(dsn) as conn:
            rows = conn.execute(
                "SELECT decision FROM screenings WHERE collection_id=%s AND paper_id=%s",
                (cid, pid),
            ).fetchall()
        # Withdrawing your judgement is yours to do; removing someone else's is not.
        assert [r[0] for r in rows] == ["include"]


class TestAgreementEndpoint:
    def test_agreement_is_absent_below_the_threshold_not_estimated(self, dsn: str) -> None:
        pids = seed(dsn, 5)
        ada = client_for(dsn, "ada@example.com")
        cid = ada.post(
            "/api/collections", json={"name": "R", "screening_mode": "blind"}
        ).json()["id"]
        token = ada.post(f"/api/collections/{cid}/invites", json={"role": "screener"}).json()[
            "token"
        ]
        grace = client_for(dsn, "grace@example.com")
        grace.post(f"/api/collections/invites/{token}/accept")
        for pid in pids:
            ada.put(f"/api/collections/{cid}/screenings/{pid}", json={"decision": "include"})
            grace.put(f"/api/collections/{cid}/screenings/{pid}", json={"decision": "include"})

        report = ada.get(f"/api/collections/{cid}/agreement").json()
        assert report["raters"] == 2
        assert report["multiply_screened"] == 5
        # Same refusal the bench harness applies to unstable percentiles.
        assert report["alpha"]["alpha"] is None
        assert report["pairwise_cohen"][0]["kappa"] is None


class TestBlindingSurvivesExport:
    """The UI is not the only way out of the database.

    A CSV download that carries a co-screener's notes defeats blinding more
    thoroughly than any UI leak, because it does not require being subtle —
    and this export shipped BEFORE collaboration, deliberately including notes.
    """

    def _blind_pair(self, dsn: str):  # type: ignore[no-untyped-def]
        pids = seed(dsn, 2)
        ada = client_for(dsn, "ada@example.com")
        cid = ada.post(
            "/api/collections", json={"name": "R", "screening_mode": "blind"}
        ).json()["id"]
        token = ada.post(f"/api/collections/{cid}/invites", json={"role": "screener"}).json()[
            "token"
        ]
        grace = client_for(dsn, "grace@example.com")
        grace.post(f"/api/collections/invites/{token}/accept")
        ada.put(
            f"/api/collections/{cid}/screenings/{pids[0]}",
            json={"decision": "include", "note": "ADA PRIVATE REASONING"},
        )
        grace.put(
            f"/api/collections/{cid}/screenings/{pids[0]}",
            json={"decision": "exclude", "note": "GRACE PRIVATE REASONING"},
        )
        return ada, grace, cid, pids

    def test_a_screener_export_cannot_read_a_co_screeners_notes(self, dsn: str) -> None:
        _ada, grace, cid, _pids = self._blind_pair(dsn)
        body = grace.get(f"/api/collections/{cid}/export.csv").content.decode("utf-8-sig")
        assert "GRACE PRIVATE REASONING" in body  # her own, correctly
        assert "ADA PRIVATE REASONING" not in body  # never someone else's

    def test_an_owner_export_carries_everything(self, dsn: str) -> None:
        ada, _grace, cid, _pids = self._blind_pair(dsn)
        body = ada.get(f"/api/collections/{cid}/export.csv").content.decode("utf-8-sig")
        # Owners already read every note at reconciliation; withholding them
        # here would protect nothing and break the export they actually need.
        assert "ADA PRIVATE REASONING" in body
        assert "GRACE PRIVATE REASONING" in body
        assert "screener" in body.splitlines()[0]

    def test_the_collection_view_is_scoped_too(self, dsn: str) -> None:
        """Same boundary, other route — the leak would have been identical."""
        _ada, grace, cid, _pids = self._blind_pair(dsn)
        papers = grace.get(f"/api/collections/{cid}").json()["papers"]
        assert all(p["note"] != "ADA PRIVATE REASONING" for p in papers)
        assert any(p["note"] == "GRACE PRIVATE REASONING" for p in papers)

    def test_bibtex_export_is_scoped_as_well(self, dsn: str) -> None:
        _ada, grace, cid, _pids = self._blind_pair(dsn)
        # BibTeX carries no notes, but it DOES reveal which papers someone
        # marked include — scoping it keeps the two exports consistent rather
        # than leaving one path narrower than the other.
        assert grace.get(f"/api/collections/{cid}/export.bib").status_code == 200


class TestAggregateLeaks:
    """A COUNT leaks too.

    The export bug was a query that was correct under one-decision-per-paper
    and stopped being correct when that changed. These are the same shape one
    level up: totals that aggregate across screeners reveal in bulk what
    blinding withholds per paper.
    """

    def test_collection_card_counts_are_yours_not_the_teams(self, dsn: str) -> None:
        pids = seed(dsn, 4)
        ada = client_for(dsn, "ada@example.com")
        cid = ada.post(
            "/api/collections", json={"name": "R", "screening_mode": "blind"}
        ).json()["id"]
        token = ada.post(f"/api/collections/{cid}/invites", json={"role": "screener"}).json()[
            "token"
        ]
        grace = client_for(dsn, "grace@example.com")
        grace.post(f"/api/collections/invites/{token}/accept")

        for pid in pids:  # Ada screens all four, all include
            ada.put(f"/api/collections/{cid}/screenings/{pid}", json={"decision": "include"})
        grace.put(f"/api/collections/{cid}/screenings/{pids[0]}", json={"decision": "exclude"})

        card = grace.get("/api/collections").json()[0]
        # Grace has decided ONE paper. A card reading "4 included" would tell
        # her Ada's verdict on three papers she has not looked at yet.
        assert card["screened"] == 1
        assert card["included"] == 0
        assert card["excluded"] == 1
        # Volume is fine — it says how much work exists, never what anyone
        # concluded.
        assert card["team_screened"] == 5
        assert card["screener_count"] == 2

    def test_public_stats_does_not_report_private_screening_activity(
        self, dsn: str
    ) -> None:
        pid = seed(dsn, 1)[0]
        ada = client_for(dsn, "ada@example.com")
        cid = ada.post("/api/collections", json={"name": "Private"}).json()["id"]
        ada.put(f"/api/collections/{cid}/screenings/{pid}", json={"decision": "include"})

        anon = TestClient(app)
        with anon:
            body = anon.get("/api/stats").json()
        # /api/stats is unauthenticated — the landing page reads the corpus
        # size from it before anyone signs in. It may describe the CORPUS and
        # never what users did with it. On a two-user instance, a global
        # "included" count minus your own is exactly the other person's.
        assert "papers" in body
        assert "screened" not in body
        assert "included" not in body


class TestResolverRoleAndSelfResolution:
    """Who breaks a tie, and whether the asymmetry is visible.

    In real systematic review the tie-breaker is a third party, precisely
    because the two disagreeing parties should not adjudicate themselves. In a
    two-person collection there IS no third party, so this is recorded rather
    than prevented — blocking it would deadlock the exact case it exists for.
    """

    def _conflict(self, dsn: str, extra_role: str | None = None):  # type: ignore[no-untyped-def]
        pid = seed(dsn, 1)[0]
        ada = client_for(dsn, "ada@example.com")
        cid = ada.post(
            "/api/collections", json={"name": "R", "screening_mode": "blind"}
        ).json()["id"]
        tok = ada.post(f"/api/collections/{cid}/invites", json={"role": "screener"}).json()[
            "token"
        ]
        grace = client_for(dsn, "grace@example.com")
        grace.post(f"/api/collections/invites/{tok}/accept")
        ada.put(f"/api/collections/{cid}/screenings/{pid}", json={"decision": "include"})
        grace.put(f"/api/collections/{cid}/screenings/{pid}", json={"decision": "exclude"})
        third = None
        if extra_role:
            t2 = ada.post(f"/api/collections/{cid}/invites", json={"role": extra_role}).json()[
                "token"
            ]
            third = client_for(dsn, "sam@example.com")
            third.post(f"/api/collections/invites/{t2}/accept")
        return ada, grace, third, cid, pid

    def test_an_owner_who_screened_is_flagged_as_an_interested_party(self, dsn: str) -> None:
        ada, _grace, _t, cid, pid = self._conflict(dsn)
        out = ada.put(
            f"/api/collections/{cid}/conflicts/{pid}", json={"decision": "include"}
        ).json()
        # Ada was one of the two who disagreed and ruled in her own favour.
        # Permitted — there is nobody else — but a reader of this review is
        # entitled to know.
        assert out["self_resolved"] is True

    def test_a_neutral_resolver_is_not_flagged(self, dsn: str) -> None:
        _ada, _grace, sam, cid, pid = self._conflict(dsn, extra_role="resolver")
        assert sam is not None
        out = sam.put(
            f"/api/collections/{cid}/conflicts/{pid}", json={"decision": "exclude"}
        ).json()
        # A third party who never screened this paper is the methodologically
        # correct arbitrator, and the record says so.
        assert out["self_resolved"] is False

    def test_a_resolver_can_adjudicate_without_administering(self, dsn: str) -> None:
        _ada, _grace, sam, cid, _pid = self._conflict(dsn, extra_role="resolver")
        assert sam is not None
        # Resolving: yes. Inviting people: no. That separation is what lets a
        # supervisor arbitrate without being handed the keys.
        assert sam.get(f"/api/collections/{cid}/conflicts").status_code == 200
        assert sam.post(f"/api/collections/{cid}/invites", json={}).status_code == 404


class TestConflictListIsAlsoBlinded:
    """"This paper is contested" is a signal — arguably a stronger one than a
    single decision, because it says the paper is hard."""

    def test_a_screener_sees_conflicts_only_on_papers_they_decided(self, dsn: str) -> None:
        pids = seed(dsn, 2)
        ada = client_for(dsn, "ada@example.com")
        cid = ada.post(
            "/api/collections", json={"name": "R", "screening_mode": "blind"}
        ).json()["id"]
        tok = ada.post(f"/api/collections/{cid}/invites", json={"role": "screener"}).json()[
            "token"
        ]
        grace = client_for(dsn, "grace@example.com")
        grace.post(f"/api/collections/invites/{tok}/accept")
        sam_tok = ada.post(f"/api/collections/{cid}/invites", json={"role": "screener"}).json()[
            "token"
        ]
        sam = client_for(dsn, "sam@example.com")
        sam.post(f"/api/collections/invites/{sam_tok}/accept")

        # Ada and Grace disagree about BOTH papers. Sam has decided only one.
        for pid in pids:
            ada.put(f"/api/collections/{cid}/screenings/{pid}", json={"decision": "include"})
            grace.put(f"/api/collections/{cid}/screenings/{pid}", json={"decision": "exclude"})
        sam.put(f"/api/collections/{cid}/screenings/{pids[0]}", json={"decision": "maybe"})

        seen = sam.get(f"/api/collections/{cid}/conflicts").json()
        assert seen["scoped"] is True
        # Learning that pids[1] is contested would tell Sam it is a hard paper
        # before he has looked at it.
        assert [c["paper_id"] for c in seen["conflicts"]] == [pids[0]]

    def test_a_resolver_sees_the_whole_queue(self, dsn: str) -> None:
        pids = seed(dsn, 2)
        ada = client_for(dsn, "ada@example.com")
        cid = ada.post(
            "/api/collections", json={"name": "R", "screening_mode": "blind"}
        ).json()["id"]
        tok = ada.post(f"/api/collections/{cid}/invites", json={"role": "screener"}).json()[
            "token"
        ]
        grace = client_for(dsn, "grace@example.com")
        grace.post(f"/api/collections/invites/{tok}/accept")
        for pid in pids:
            ada.put(f"/api/collections/{cid}/screenings/{pid}", json={"decision": "include"})
            grace.put(f"/api/collections/{cid}/screenings/{pid}", json={"decision": "exclude"})

        seen = ada.get(f"/api/collections/{cid}/conflicts").json()
        # Adjudicating a queue you cannot see is not a job.
        assert seen["scoped"] is False
        assert sorted(c["paper_id"] for c in seen["conflicts"]) == sorted(pids)


class TestDetailViewIsAListOfPapers:
    """PAPERS_SQL returns one row per (paper, screener), which is right for an
    export and wrong for this view. With see_all an owner saw the same paper
    once per colleague, and the UI rendered duplicate React keys — the visible
    symptom of a shape mismatch rather than a permission bug."""

    def test_owner_sees_each_paper_once(self, dsn: str) -> None:
        pids = seed(dsn, 3)
        ada = client_for(dsn, "ada@example.com")
        cid = ada.post(
            "/api/collections", json={"name": "R", "screening_mode": "blind"}
        ).json()["id"]
        tok = ada.post(f"/api/collections/{cid}/invites", json={"role": "screener"}).json()[
            "token"
        ]
        grace = client_for(dsn, "grace@example.com")
        grace.post(f"/api/collections/invites/{tok}/accept")
        for pid in pids:
            ada.put(f"/api/collections/{cid}/screenings/{pid}", json={"decision": "include"})
            grace.put(f"/api/collections/{cid}/screenings/{pid}", json={"decision": "exclude"})

        papers = ada.get(f"/api/collections/{cid}").json()["papers"]
        ids = [p["id"] for p in papers]
        assert len(ids) == len(set(ids)) == 3
        # And they are HER calls, not a mixture.
        assert {p["decision"] for p in papers} == {"include"}

    def test_the_export_still_carries_every_screener(self, dsn: str) -> None:
        """The two shapes are deliberate: a list of papers here, a list of
        decisions there."""
        pid = seed(dsn, 1)[0]
        ada = client_for(dsn, "ada@example.com")
        cid = ada.post(
            "/api/collections", json={"name": "R", "screening_mode": "blind"}
        ).json()["id"]
        tok = ada.post(f"/api/collections/{cid}/invites", json={"role": "screener"}).json()[
            "token"
        ]
        grace = client_for(dsn, "grace@example.com")
        grace.post(f"/api/collections/invites/{tok}/accept")
        ada.put(f"/api/collections/{cid}/screenings/{pid}", json={"decision": "include"})
        grace.put(f"/api/collections/{cid}/screenings/{pid}", json={"decision": "exclude"})

        body = ada.get(f"/api/collections/{cid}/export.csv").content.decode("utf-8-sig")
        rows = [r for r in body.splitlines()[1:] if r.strip()]
        assert len(rows) == 2  # one per screener
