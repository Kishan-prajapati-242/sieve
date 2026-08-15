// Where the paper came from, readable WITHOUT motion, hover, or colour
// vision alone.
//
// This is the requirement, not the flourish (Kishan, 2026-08-14): Phase 4
// ships a demo URL a reviewer may open on a phone or screenshot into a
// deck. Hover reveals nothing in either. So a STILL of a hybrid list has to
// show the top rows drawing from both arms and the tail from one.
//
// One hue per arm carries that: amber for keyword, violet for semantic.
// Neither is the link blue, so the accent budget stays at three. The rank
// number lives inside the chip, and an arm that missed the paper renders a
// muted chip with an em-dash rather than being omitted — absence has to be
// as visible as presence, or a one-armed row just looks like a short row.
//
// No product surveyed explains its ranking at all (visual-sheet.html §7),
// so there is no convention to borrow here; this is the defensible part.
export function ProvenanceChips({
  bm25Rank,
  vectorRank,
}: {
  bm25Rank: number | null;
  vectorRank: number | null;
}) {
  if (bm25Rank === null && vectorRank === null) return null;
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] font-medium">
      <Chip label="keyword" rank={bm25Rank} tone="amber" />
      <Chip label="semantic" rank={vectorRank} tone="violet" />
    </span>
  );
}

function Chip({
  label,
  rank,
  tone,
}: {
  label: string;
  rank: number | null;
  tone: "amber" | "violet";
}) {
  const on =
    tone === "amber"
      ? "bg-keyword-950 text-amber-800 ring-amber-200"
      : "bg-semantic-950 text-violet-800 ring-violet-200";
  const off = "bg-ink-850 text-ink-500 ring-slate-200";
  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 ring-1 ring-inset ${
        rank === null ? off : on
      }`}
      title={rank === null ? `not found by ${label}` : `${label} rank ${rank}`}
    >
      {label}
      <span className="tabular-nums">{rank === null ? "—" : `#${rank}`}</span>
    </span>
  );
}
