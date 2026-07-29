// One search hit. Deliberately plain (design is Phase 4); the only strong
// visual is the retraction banner, which is DECISION-1c's whole point:
// retracted papers are shown, loudly, so a reviewer excludes them on purpose.
// The abstract uses a native <details> — collapsed/expandable with zero
// component state and keyboard/screen-reader behavior for free.
import type { SearchResult } from "./api";

const MAX_AUTHORS = 10;

function authorLine(authors: string[]): string {
  if (authors.length <= MAX_AUTHORS) return authors.join(", ");
  return `${authors.slice(0, MAX_AUTHORS).join(", ")}, +${authors.length - MAX_AUTHORS} more`;
}

export function ResultCard({ result }: { result: SearchResult }) {
  return (
    <li className="border border-gray-300 rounded p-3 space-y-1">
      <div className="text-sm text-gray-500">
        #{result.rank} · score {result.score.toFixed(4)}
      </div>
      {result.is_retracted && (
        <div role="alert" className="border border-red-600 bg-red-50 text-red-800 rounded px-2 py-1 text-sm font-semibold">
          Retracted — shown so it can be excluded deliberately; check what cites it.
        </div>
      )}
      <h2 className="font-semibold">{result.title}</h2>
      {result.authors && <div className="text-sm">{authorLine(result.authors)}</div>}
      <div className="text-sm text-gray-600">
        {result.year ?? "year unknown"}
        {result.venue && <> · {result.venue}</>}
        <> · {result.citation_count} citations</>
        {result.doi && (
          <>
            {" · "}
            <a
              href={`https://doi.org/${result.doi}`}
              target="_blank"
              rel="noreferrer"
              className="underline"
            >
              DOI
            </a>
          </>
        )}
      </div>
      {result.abstract && (
        <details>
          <summary className="cursor-pointer text-sm text-gray-500">Abstract</summary>
          <p className="text-sm mt-1">{result.abstract}</p>
        </details>
      )}
    </li>
  );
}
