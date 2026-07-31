"""Seed boilerplate_abstracts — the hand-curated part of the corpus cleanup.

Two automatic rules plus one hand-reviewed list, because the measurement
showed the automatic signals disagree with each other above ~100 chars:

  RULE_SHORT   length < 50 — nothing at that length is a description
                (the extreme in this corpus: an abstract that is the single
                character "P").
  RULE_SHARED  shared by >= 5 papers AND length < 100 — repository and
                platform artifacts: "International audience" (801 papers,
                HAL's deposit boilerplate), "Monthly data release from
                WikiPathways.org" (442), "Click to increase image size".

  HAND         shared by >= 5 papers AND length >= 100, reviewed one by one.
               This band is MOSTLY LEGITIMATE (real structured abstracts,
               dataset descriptions, a textbook description shared across
               editions), so an automatic rule here would delete real
               signal. Only the prefixes below are boilerplate; everything
               else in the band is deliberately kept, and `reason` records
               the judgment for each.

Prefixes rather than md5 literals: an md5 list is unreadable, and the point
of a hand-curated table is that a human can audit it. Each prefix is
matched with LIKE against the stored abstract.

Run inside compose (test service has the DB):
    docker compose run --rm --no-deps -e DATABASE_URL=... test \\
        python -m bench.seed_boilerplate [--dry-run]
"""

import argparse
import os

import psycopg

# Boilerplate found in the shared>=5, length>=100 band, by hand (2026-07-31).
HAND_BOILERPLATE: list[tuple[str, str]] = [
    # Publisher / platform / repository furniture
    ("An abstract is not available for this content", "paywall placeholder"),
    ("A summary is not available for this content", "paywall placeholder"),
    ("Our website uses cookies to enhance your experience", "cookie banner"),
    ("Proceedings of the National Academy of Sciences (PNAS), a peer reviewed", "publisher blurb"),
    ("Inderscience is a global company", "publisher blurb"),
    ("Microbiology Society journals contain high-quality", "publisher blurb"),
    ("AccessScience is an authoritative and dynamic online resource", "platform blurb"),
    ("Frontiers Events is a rapidly growing calendar", "platform blurb"),
    ("DOAJ is a unique and extensive index", "platform blurb"),
    ("PharmacyLibrary", "platform blurb"),
    ("Zotero is a free citation management tool", "tool blurb"),
    ("Features and functions of EndNote", "tool blurb"),
    ("PubMed Clinical Queries provides specialized searches", "tool blurb"),
    ("Searchable abstracts of presentations at key conferences", "conference index furniture"),
    ("三仓出版社主要从事", "publisher blurb (zh)"),
    ("ZENODO INTELLIGENCE Enhanced Version", "repository injection"),
    ("This thesis was scanned from the print manuscript", "repository preservation note"),
    ("Maximise the visibility of your research outputs", "repository marketing"),
    ("tware and dataset uploads. Any string will be accepted", "repository help text"),
    (
        "Research data dictionary. This dataset follows the Juan de la Serna",
        "attribution boilerplate",
    ),
    ("# Taxonomía Serna", "series attribution boilerplate"),
    (
        "The Information Technology Laboratory (ITL) at the National Institute",
        "institutional blurb",
    ),
    ("A list of concepts from the standardized vocabulary", "vocabulary stub"),
    # Library research guides (LibGuides) — guide furniture, not papers
    (
        "This guide will give you information on, and walk you through, all aspects of the ETD",
        "libguide",
    ),
    ("A guide to all aspects of PRISMA Database", "libguide"),
    ("This guide is for the subject Loss and Bereavement", "libguide"),
    ("This LibGuide offers an overview", "libguide"),
    ("All scholarly research is built upon knowledge of the past literature", "libguide"),
    ("This guide offers local services and resources", "libguide"),
    ("Guide lists all the steps one needs to follow when posting an e-Thesis", "libguide"),
    ("Guide to text mining resources available through Emory Libraries", "libguide"),
    ("Subject Guide for English", "libguide"),
    ("This guide is for all majors researching psychology", "libguide"),
    ("This guide will help you get the most out of the library database Credo", "libguide"),
    ("Drop-in Workshop on how to find literary criticisms", "library workshop notice"),
    # Series-level blurbs repeated verbatim across many "papers"
    ("AUGMANITAI Ethical Disclaimer", "series legal disclaimer"),
    ("Technical working paper examining standardization gaps", "series blurb"),
    ("Working paper from the AUGMANITAI research project", "series blurb"),
    ("Contribution to the AUGMANITAI terminological research program", "series blurb"),
    ("Preprint from an independent research program on formal concept analysis", "series blurb"),
    ("Americanae nace como un proyecto conjunto", "project blurb (es)"),
    ("Mental Health America is holding its annual conference", "event announcement"),
    ("Presented at the “2026 NAIRR Annual Meeting”", "venue note, no description"),
    ("Protocol Repository and Exporting", "product documentation"),
]

SEED_SQL = """
INSERT INTO boilerplate_abstracts (abstract_md5, sample, reason, n_at_seed)
SELECT md5(abstract), left(regexp_replace(abstract, '\\s+', ' ', 'g'), 120), %(reason)s, count(*)
FROM papers
WHERE abstract IS NOT NULL AND {predicate}
GROUP BY md5(abstract), left(regexp_replace(abstract, '\\s+', ' ', 'g'), 120)
ON CONFLICT (abstract_md5) DO NOTHING
"""

SHARED_MD5S = """
    md5(abstract) IN (
        SELECT md5(abstract) FROM papers WHERE abstract IS NOT NULL
        GROUP BY 1 HAVING count(*) >= 5
    )
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        before = conn.execute("SELECT count(*) FROM boilerplate_abstracts").fetchone()
        assert before is not None

        conn.execute(
            SEED_SQL.format(predicate="length(abstract) < 50"),
            {"reason": "rule: length < 50, too short to be a description"},
        )
        conn.execute(
            SEED_SQL.format(predicate=f"length(abstract) < 100 AND {SHARED_MD5S}"),
            {"reason": "rule: shared by >=5 papers and length < 100"},
        )
        for prefix, why in HAND_BOILERPLATE:
            conn.execute(
                SEED_SQL.format(predicate="abstract LIKE %(like)s AND " + SHARED_MD5S),
                {"reason": f"hand-reviewed (shared>=5, long): {why}", "like": prefix + "%"},
            )

        after = conn.execute("SELECT count(*) FROM boilerplate_abstracts").fetchone()
        affected = conn.execute(
            """
            SELECT count(*) FROM papers p
            JOIN boilerplate_abstracts b ON b.abstract_md5 = md5(p.abstract)
            """
        ).fetchone()
        by_reason = conn.execute(
            """
            SELECT split_part(reason, ':', 1) AS kind, count(*) AS hashes, sum(n_at_seed) AS papers
            FROM boilerplate_abstracts GROUP BY 1 ORDER BY 3 DESC
            """
        ).fetchall()
        assert after is not None and affected is not None

        if args.dry_run:
            conn.execute("DELETE FROM boilerplate_abstracts")
            print("(dry run: rolled back)")

    print(f"blocklist hashes: {before[0]} -> {after[0]}")
    print(f"papers affected (embed title-only): {affected[0]}")
    for kind, hashes, papers in by_reason:
        print(f"  {kind}: {hashes} hashes, {papers} papers")


if __name__ == "__main__":
    main()
