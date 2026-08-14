// The reduced-motion path, tested rather than screenshotted.
//
// This is the PRIMARY path for a reviewer opening the demo URL on a phone
// with Reduce Motion on, and for any still of the app. Nothing about it is
// visible in normal test runs, so it is exactly the kind of thing a
// refactor breaks in silence — the same argument that put a test on
// role="alert" for the retraction badge.
import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SearchResult } from "./api";
import { ResultCard } from "./ResultCard";
import { arrivalDelay, rowMotionProps } from "./motion";
import { renderWith } from "./testutils";

function setReducedMotion(reduce: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches: reduce && query.includes("prefers-reduced-motion"),
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
}

function makeResult(overrides: Partial<SearchResult> = {}): SearchResult {
  return {
    rank: 1,
    score: 0.4,
    id: 1,
    doi: null,
    title: "A paper",
    authors: null,
    abstract: null,
    year: 2023,
    venue: null,
    citation_count: 0,
    is_retracted: false,
    bm25_rank: null,
    vector_rank: null,
    sources: null,
    ...overrides,
  };
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe("reduced motion", () => {
  it("suppresses the entrance, not just the layout animation", () => {
    // The bug this pins: disabling `layout` alone leaves initial opacity 0
    // and the arrival delay intact, so a reader who asked for no motion
    // still watches rows fade up one at a time.
    const moving = rowMotionProps(false, 0.55);
    expect(moving.layout).toBe("position");
    expect(moving.initial).toBe("initial"); // entrance variant runs
    expect(moving.custom).toBe(0.55); // and it is delayed

    const still = rowMotionProps(true, 0.55);
    expect(still.layout).toBe(false);
    expect(still.initial).toBe(false); // mount at the final state
    expect(still.custom).toBe(0); // delay dropped with it
  });

  it("drops the delay for EVERY arrival, not just the first", () => {
    // 15 arrivals with survivors is the real bm25 -> hybrid transition; the
    // topmost row carries the largest delay and is the one that would be
    // left invisible longest.
    const delays = Array.from({ length: 15 }, (_, i) => arrivalDelay(i, 15, true));
    expect(Math.max(...delays)).toBeGreaterThan(0.6); // motion on: real waits
    expect(delays.every((d) => rowMotionProps(true, d).custom === 0)).toBe(true);
  });

  it("keeps fusion readable with motion off, since a still is all a reviewer gets", () => {
    setReducedMotion(true);
    renderWith(
      <ResultCard result={makeResult({ bm25_rank: 4, vector_rank: 1, sources: ["bm25", "vector"] })} />,
    );
    // Both arms named and ranked, with no hover and no animation.
    expect(screen.getByTitle("keyword rank 4")).toBeInTheDocument();
    expect(screen.getByTitle("semantic rank 1")).toBeInTheDocument();
  });

  it("shows a missing arm as present-and-empty, not as an absence", () => {
    setReducedMotion(true);
    renderWith(
      <ResultCard result={makeResult({ bm25_rank: 7, vector_rank: null, sources: ["bm25"] })} />,
    );
    // A one-armed row must not just look like a short row: the chip is
    // rendered muted with an em-dash where the rank would be.
    const missing = screen.getByTitle("not found by semantic");
    expect(missing).toBeInTheDocument();
    expect(within(missing).getByText("—")).toBeInTheDocument();
  });

  it("still renders the row's content when motion is off", () => {
    setReducedMotion(true);
    renderWith(<ResultCard result={makeResult({ title: "Visible with motion off" })} />);
    expect(screen.getByText("Visible with motion off")).toBeVisible();
  });
});
