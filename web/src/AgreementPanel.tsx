// Inter-rater agreement.
//
// The hard part of this panel is not rendering a number, it is rendering the
// ABSENCE of one well. Both statistics are deliberately undefined in cases
// that look like success — two screeners who called everything `include` agree
// perfectly and chance predicts exactly that, so kappa is 0/0 — and a panel
// that showed a blank there would read as "not computed yet" rather than "this
// cannot be computed, here is why".
//
// So every missing value carries its reason, and the sample size sits next to
// every present one. That is the same rule the bench harness applies to
// unstable percentiles: absent beats estimated, and a figure without its n is
// a figure nobody can check.
import { useQuery } from "@tanstack/react-query";
import { getAgreement, getMembers } from "./api";

/** Landis & Koch's conventional bands. Shown as words because "0.62" means
 *  nothing to most readers, and labelled as a convention rather than a fact
 *  because that is what it is. */
function band(k: number): string {
  if (k < 0) return "worse than chance";
  if (k < 0.2) return "slight";
  if (k < 0.4) return "fair";
  if (k < 0.6) return "moderate";
  if (k < 0.8) return "substantial";
  return "almost perfect";
}

export function AgreementPanel({ collectionId }: { collectionId: number }) {
  const { data } = useQuery({
    queryKey: ["agreement", collectionId],
    queryFn: () => getAgreement(collectionId),
  });
  const { data: members } = useQuery({
    queryKey: ["members", collectionId],
    queryFn: () => getMembers(collectionId),
  });

  if (!data) return <div className="h-20 animate-pulse rounded-xl bg-ink-880" />;

  const name = (id: number) =>
    members?.members.find((m) => m.user_id === id)?.email.split("@")[0] ?? `#${id}`;

  return (
    <div className="hairline rounded-2xl border bg-ink-880/70 p-5 backdrop-blur-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-mono text-eyebrow uppercase text-ink-400">Agreement</h2>
        <span className="font-mono text-[11px] text-ink-500">
          {data.multiply_screened} of {data.screened_papers} double-screened
        </span>
      </div>

      {/* Krippendorff's alpha — the headline, because it is the only statistic
          here that admits a variable number of raters per paper. */}
      <div className="mt-4">
        {data.alpha.alpha !== null ? (
          <div className="flex items-baseline gap-3">
            <span className="font-mono text-h2 text-ink-50">{data.alpha.alpha.toFixed(3)}</span>
            <span className="text-sm text-ink-300">
              Krippendorff&apos;s α · {band(data.alpha.alpha)}
            </span>
            <span className="ml-auto font-mono text-[11px] text-ink-500">
              n={data.alpha.n_items}
            </span>
          </div>
        ) : (
          <div className="hairline rounded-lg border bg-ink-900 px-3 py-2.5">
            <p className="font-mono text-[11px] uppercase tracking-wider text-ink-400">
              Krippendorff&apos;s α — not reportable
            </p>
            <p className="mt-1 text-sm text-ink-300">{data.alpha.undefined}</p>
          </div>
        )}
        <p className="mt-2 text-xs leading-relaxed text-ink-500">
          α rather than Fleiss&apos; κ because papers pick up extra screeners when
          they are contentious — filtering to an equal number of raters would bias
          the very thing being measured.
        </p>
      </div>

      {data.pairwise_cohen.length > 0 && (
        <div className="hairline mt-5 border-t pt-4">
          <p className="font-mono text-[10px] uppercase tracking-wider text-ink-400">
            Per pair · Cohen&apos;s κ
          </p>
          <ul className="mt-3 flex flex-col gap-1.5">
            {data.pairwise_cohen.map((p) => (
              <li
                key={`${p.user_a}-${p.user_b}`}
                className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm"
              >
                <span className="font-mono text-xs text-ink-300">
                  {name(p.user_a)} ↔ {name(p.user_b)}
                </span>
                {p.kappa !== null ? (
                  <>
                    <span className="font-mono text-ink-50">{p.kappa.toFixed(3)}</span>
                    <span className="text-xs text-ink-400">{band(p.kappa)}</span>
                  </>
                ) : (
                  <span className="text-xs text-ink-500">{p.undefined}</span>
                )}
                <span className="ml-auto font-mono text-[11px] text-ink-600">n={p.n}</span>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs leading-relaxed text-ink-500">
            A pair&apos;s κ is the number you can act on — it names who diverges. It
            is withheld below 30 shared papers, where one changed call moves it by
            more than a tenth.
          </p>
        </div>
      )}
    </div>
  );
}
