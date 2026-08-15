// The landing page.
//
// EVERY NUMBER HERE IS MEASURED and carries its provenance in the note line,
// because CLAUDE.md's rule ("never write a number that was not measured")
// does not stop at the bench/ directory — a marketing surface is the easiest
// place in a project to print something nobody checked. Where a figure is
// unpublished, it is absent rather than estimated: hybrid p50 is not on this
// page because its stability gate refused a point estimate.
import { FusionDiagram } from "./FusionDiagram";
import {
  Button,
  ButtonLink,
  Card,
  Container,
  CountUp,
  Eyebrow,
  Reveal,
  Section,
  Stat,
  useCorpusSize,
} from "./ui";

/** Measured figures, each with where it came from. Kept as data so the
 *  provenance travels with the value instead of living in a comment.
 *
 *  The corpus size is a PARAMETER, not a literal: the PubMed pull takes it
 *  to ~200,000 and a typed number would then describe a corpus that no
 *  longer exists — on the most visible surface the project has.
 */
function stats(corpus: number | null) {
  return [
    {
      value: corpus === null ? "—" : <CountUp to={corpus} />,
      label: "papers indexed",
      note: "live, from /api/stats",
    },
    { value: "0.9782", label: "recall@20 vs exact scan", note: "ef=160 · DECISION-4b" },
    { value: "7.7×", label: "faster than exact scan", note: "paired, CI [7.5, 8.0]" },
    { value: "0", label: "external services", note: "no redis · no ES · no vector db" },
  ];
}

const ARMS = [
  {
    name: "Keyword",
    tone: "keyword" as const,
    sql: "ts_rank_cd(fts, q)",
    body: "Postgres full-text search over a weighted tsvector, title above abstract. Finds the papers that use your words.",
    misses: "Misses the paper that says “de-identification” when you typed “anonymisation”.",
  },
  {
    name: "Semantic",
    tone: "semantic" as const,
    sql: "embedding <=> query",
    body: "384-dim embeddings in pgvector, HNSW index, ONNX on CPU. Finds the papers that mean your words.",
    misses: "Misses the exact acronym match that any keyword index would catch instantly.",
  },
];

const PIPELINE = [
  { k: "01", t: "Ingest", d: "OpenAlex and arXiv behind per-source token buckets, full-jitter retry, explicit timeouts." },
  { k: "02", t: "Queue", d: "A Postgres table and SELECT … FOR UPDATE SKIP LOCKED. Idempotent, resumable, SIGKILL-proven." },
  { k: "03", t: "Deduplicate", d: "A five-step cascade from exact ids to trigram titles, union-find grouped, every merge reversible." },
  { k: "04", t: "Embed", d: "bge-small-en-v1.5 through ONNX Runtime on CPU, checkpointed so a killed run resumes exactly." },
  { k: "05", t: "Retrieve", d: "Both arms in one SQL statement, fused by reciprocal rank at k=60. Raw SQL, readable EXPLAIN." },
];

export function LandingPage() {
  const corpus = useCorpusSize();
  return (
    <div className="relative">
      {/* ---------------- HERO ---------------- */}
      <div className="field-glow relative overflow-hidden">
        <div className="grid-lines absolute inset-0 h-[560px]" aria-hidden="true" />
        <Container className="relative z-10 pb-20 pt-24 sm:pt-32">
          <Reveal>
            <Eyebrow>Hybrid retrieval over academic literature</Eyebrow>
          </Reveal>

          <Reveal delay={0.06}>
            <h1 className="mt-7 max-w-3xl text-h1 font-semibold text-ink-50 sm:text-display">
              Two rankers disagree.
              <br />
              <span className="text-fusion">Sieve settles it.</span>
            </h1>
          </Reveal>

          <Reveal delay={0.12}>
            <p className="mt-6 max-w-xl text-[17px] leading-relaxed text-ink-300">
              Keyword search finds the papers that use your words. Vector search finds the
              ones that mean them. Sieve runs both against{" "}
              <span className="font-mono text-ink-100">{corpus.text}</span> papers and fuses
              the rankings, so the paper neither arm ranked first can still come first.
            </p>
          </Reveal>

          <Reveal delay={0.18}>
            <div className="mt-9 flex flex-wrap items-center gap-3">
              <ButtonLink to="/search" size="lg">
                Try the search
              </ButtonLink>
              <ButtonLink to="/signup" size="lg" variant="secondary">
                Create an account
              </ButtonLink>
            </div>
          </Reveal>

          <Reveal delay={0.24} className="mt-20">
            <Card className="overflow-hidden p-8 sm:p-10">
              <FusionDiagram className="mx-auto h-[260px] w-full max-w-3xl" />
              <p className="hairline mt-8 border-t pt-5 font-mono text-[11px] uppercase tracking-wider text-ink-500">
                Reciprocal rank fusion · k=60 · depth 200 per arm
              </p>
            </Card>
          </Reveal>
        </Container>
      </div>

      {/* ---------------- MEASURED ---------------- */}
      <Section>
        <Container>
          <div className="grid grid-cols-2 gap-10 lg:grid-cols-4">
            {stats(corpus.value).map((s, i) => (
              <Reveal key={s.label} delay={i * 0.06}>
                <Stat {...s} />
              </Reveal>
            ))}
          </div>
          <Reveal delay={0.3}>
            <p className="mt-12 max-w-2xl text-sm leading-relaxed text-ink-400">
              Every figure on this page comes from a script in{" "}
              <code className="font-mono text-ink-300">bench/</code>, measured on the live
              corpus. Speedups are paired — baseline and candidate timed back to back on the
              same query — because measuring them in separate runs let machine drift inflate
              the ratio. Numbers whose stability gate failed are absent rather than rounded.
            </p>
          </Reveal>
        </Container>
      </Section>

      {/* ---------------- THE TWO ARMS ---------------- */}
      <Section>
        <Container>
          <Reveal>
            <Eyebrow>The mechanism</Eyebrow>
            <h2 className="mt-6 max-w-2xl text-h1 font-semibold text-ink-50">
              Neither arm is enough on its own.
            </h2>
          </Reveal>

          <div className="mt-14 grid gap-6 lg:grid-cols-2">
            {ARMS.map((arm, i) => (
              <Reveal key={arm.name} delay={i * 0.08}>
                <Card className="h-full p-8" interactive>
                  <div className="flex items-center gap-3">
                    <span
                      className={`size-2 rounded-full ${
                        arm.tone === "keyword" ? "bg-keyword-400" : "bg-semantic-400"
                      }`}
                      aria-hidden="true"
                    />
                    <span
                      className={`font-mono text-eyebrow uppercase ${
                        arm.tone === "keyword" ? "text-keyword-400" : "text-semantic-400"
                      }`}
                    >
                      {arm.name}
                    </span>
                  </div>
                  <p
                    className={`mt-5 rounded-lg px-3 py-2 font-mono text-sm ${
                      arm.tone === "keyword"
                        ? "bg-keyword-950 text-keyword-300"
                        : "bg-semantic-950 text-semantic-300"
                    }`}
                  >
                    {arm.sql}
                  </p>
                  <p className="mt-5 leading-relaxed text-ink-200">{arm.body}</p>
                  <p className="hairline mt-5 border-t pt-5 text-sm leading-relaxed text-ink-400">
                    {arm.misses}
                  </p>
                </Card>
              </Reveal>
            ))}
          </div>

          <Reveal delay={0.2}>
            <Card className="mt-6 bg-fusion p-px">
              <div className="rounded-[calc(var(--radius-card)-1px)] bg-ink-900 p-8">
                <span className="font-mono text-eyebrow uppercase text-fusion">Fused</span>
                <p className="mt-4 max-w-2xl leading-relaxed text-ink-100">
                  Reciprocal rank fusion scores a paper by where each arm ranked it, not by
                  each arm&apos;s score — <span className="font-mono text-ink-50">ts_rank_cd</span>{" "}
                  and cosine distance are not comparable quantities and never meet. A paper
                  ranked fourth by both arms beats one ranked first by a single arm.
                </p>
              </div>
            </Card>
          </Reveal>
        </Container>
      </Section>

      {/* ---------------- PIPELINE ---------------- */}
      <Section>
        <Container>
          <Reveal>
            <Eyebrow>What runs behind it</Eyebrow>
            <h2 className="mt-6 max-w-2xl text-h1 font-semibold text-ink-50">
              The retrieval is the easy half.
            </h2>
            <p className="mt-5 max-w-xl leading-relaxed text-ink-400">
              Getting <span className="font-mono text-ink-200">{corpus.text}</span> clean,
              deduplicated, embedded papers into Postgres is where the engineering is. No Redis, no Elasticsearch, no vector database — every one of
              those was considered and rejected in writing.
            </p>
          </Reveal>

          <div className="mt-14 grid gap-px overflow-hidden rounded-card sm:grid-cols-2 lg:grid-cols-3">
            {PIPELINE.map((s, i) => (
              <Reveal key={s.k} delay={i * 0.05} className="h-full">
                <div className="hairline h-full border bg-ink-880 p-7 transition-colors duration-200 hover:bg-ink-850">
                  <span className="font-mono text-eyebrow text-ink-500">{s.k}</span>
                  <h3 className="mt-4 text-h3 font-medium text-ink-50">{s.t}</h3>
                  <p className="mt-3 text-sm leading-relaxed text-ink-400">{s.d}</p>
                </div>
              </Reveal>
            ))}
            <Reveal delay={0.25} className="h-full">
              <div className="hairline flex h-full flex-col justify-between border bg-ink-900 p-7">
                <p className="text-sm leading-relaxed text-ink-400">
                  Every stage is measured, and the measurements are in the repo alongside the
                  code that produced them.
                </p>
                <span className="mt-6 font-mono text-eyebrow uppercase text-ink-500">
                  254 tests · forward-only migrations
                </span>
              </div>
            </Reveal>
          </div>
        </Container>
      </Section>

      {/* ---------------- CTA ---------------- */}
      <Section>
        <Container>
          <Reveal>
            <div className="hairline field-glow relative overflow-hidden rounded-card border px-8 py-20 text-center sm:px-16">
              <div className="relative z-10">
                <h2 className="mx-auto max-w-xl text-h1 font-semibold text-ink-50">
                  Search it yourself.
                </h2>
                <p className="mx-auto mt-5 max-w-md leading-relaxed text-ink-300">
                  Run the same query through each arm and watch the ranking change. Save what
                  you find to a collection.
                </p>
                <div className="mt-9 flex flex-wrap justify-center gap-3">
                  <ButtonLink to="/search" size="lg">
                    Open the search
                  </ButtonLink>
                  <ButtonLink to="/signup" size="lg" variant="secondary">
                    Create an account
                  </ButtonLink>
                </div>
              </div>
            </div>
          </Reveal>
        </Container>
      </Section>
    </div>
  );
}

export function Footer() {
  return (
    <footer className="hairline border-t py-12">
      <Container className="flex flex-wrap items-center justify-between gap-6">
        <span className="font-mono text-[11px] uppercase tracking-wider text-ink-500">
          Sieve · hybrid literature retrieval
        </span>
        <span className="font-mono text-[11px] uppercase tracking-wider text-ink-600">
          Postgres · pgvector · ONNX · FastAPI · React
        </span>
      </Container>
    </footer>
  );
}

export { Button };
