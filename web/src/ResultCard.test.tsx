// Pins the behaviors Kishan specified for the result card, not the styling:
// every field renders, the DOI link resolves, the abstract starts collapsed,
// and the retraction banner appears exactly when is_retracted says so.
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { SearchResult } from "./api";
import { ResultCard } from "./ResultCard";
import { renderWith } from "./testutils";

// ResultCard now renders the "Add to…" control, which needs the query client
// and the router. renderWith supplies both; the assertions below are
// unchanged and still behaviour-only.

function makeResult(overrides: Partial<SearchResult> = {}): SearchResult {
  return {
    rank: 3,
    score: 0.4321,
    id: 42,
    doi: "10.18653/v1/2023.acl-long.1",
    title: "Clinical text simplification with transformers",
    authors: ["Ada Lovelace", "Grace Hopper"],
    abstract: "We simplify EHR notes.",
    year: 2023,
    venue: "Proceedings of ACL 2023",
    citation_count: 40,
    is_retracted: false,
    bm25_rank: null,
    vector_rank: null,
    sources: null,
    ...overrides,
  };
}

describe("ResultCard", () => {
  it("renders every field the spec lists", () => {
    renderWith(<ResultCard result={makeResult()} />);
    expect(screen.getByText("Clinical text simplification with transformers")).toBeInTheDocument();
    expect(screen.getByText("Ada Lovelace, Grace Hopper")).toBeInTheDocument();
    expect(screen.getByText(/#3 · score 0\.4321/)).toBeInTheDocument();
    expect(screen.getByText(/2023 · Proceedings of ACL 2023 · 40 citations/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "DOI" })).toHaveAttribute(
      "href",
      "https://doi.org/10.18653/v1/2023.acl-long.1",
    );
  });

  it("collapses the abstract until opened", async () => {
    renderWith(<ResultCard result={makeResult()} />);
    expect(screen.getByText("We simplify EHR notes.")).not.toBeVisible();
    await userEvent.click(screen.getByText("Abstract"));
    expect(screen.getByText("We simplify EHR notes.")).toBeVisible();
  });

  it("warns on retracted papers and only on them", () => {
    const { rerender } = renderWith(<ResultCard result={makeResult({ is_retracted: true })} />);
    expect(screen.getByRole("alert")).toHaveTextContent(/retracted/i);
    rerender(<ResultCard result={makeResult()} />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("truncates long author lists instead of flooding the card", () => {
    const authors = Array.from({ length: 13 }, (_, i) => `Author ${i + 1}`);
    renderWith(<ResultCard result={makeResult({ authors })} />);
    expect(screen.getByText(/Author 10, \+3 more$/)).toBeInTheDocument();
    expect(screen.queryByText(/Author 11/)).not.toBeInTheDocument();
  });

  it("shows the fusion breakdown when the result carries one", () => {
    renderWith(
      <ResultCard
        result={makeResult({ bm25_rank: 4, vector_rank: 1, sources: ["bm25", "vector"] })}
      />,
    );
    expect(screen.getByText(/keyword #4/)).toBeInTheDocument();
    expect(screen.getByText(/semantic #1/)).toBeInTheDocument();
    // A ranker that missed the paper renders a dash, not a fake rank.
    renderWith(<ResultCard result={makeResult({ bm25_rank: 7, vector_rank: null, sources: ["bm25"] })} />);
    expect(screen.getByText(/semantic —/)).toBeInTheDocument();
  });

  it("shows no breakdown for non-hybrid results", () => {
    renderWith(<ResultCard result={makeResult()} />);
    expect(screen.queryByText(/keyword #/)).not.toBeInTheDocument();
  });

  it("omits optional fields without leaving artifacts", () => {
    renderWith(
      <ResultCard
        result={makeResult({ authors: null, abstract: null, doi: null, venue: null, year: null })}
      />,
    );
    expect(screen.getByText(/year unknown · 40 citations/)).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.queryByText("Abstract")).not.toBeInTheDocument();
  });
});
