// Slow-fetch feedback. placeholderData keeps the previous rows mounted so
// the layout animation has DOM to move — but it also means a fetch in
// flight and a finished one look identical, measured at 1,611ms on a cold
// hybrid query. These pin both halves of the tradeoff: silent when warm,
// visible when cold.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { useDelayedFlag } from "./useDelayedFlag";
import { renderHook } from "@testing-library/react";

describe("useDelayedFlag", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("stays false for a fetch that finishes under the threshold", () => {
    const { result, rerender } = renderHook(({ a }) => useDelayedFlag(a, 200), {
      initialProps: { a: true },
    });
    act(() => void vi.advanceTimersByTime(30)); // a warm query
    rerender({ a: false });
    expect(result.current).toBe(false); // never flashed
  });

  it("goes true once a fetch outlives the threshold", () => {
    const { result } = renderHook(() => useDelayedFlag(true, 200));
    act(() => void vi.advanceTimersByTime(199));
    expect(result.current).toBe(false);
    act(() => void vi.advanceTimersByTime(2));
    expect(result.current).toBe(true);
  });

  it("resets when the fetch ends, so it cannot latch on", () => {
    const { result, rerender } = renderHook(({ a }) => useDelayedFlag(a, 200), {
      initialProps: { a: true },
    });
    act(() => void vi.advanceTimersByTime(400));
    expect(result.current).toBe(true);
    rerender({ a: false });
    expect(result.current).toBe(false);
  });
});

describe("slow search feedback", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("marks the standing results busy while the next mode is in flight", async () => {
    const body = (total: number, kind: string) => ({
      query: "q",
      mode: "bm25",
      took_ms: 1.0,
      timings: { embed_ms: null, retrieve_ms: 1.0, serialize_ms: 0.1 },
      ef_search: null,
      total: { value: total, kind },
      results: [
        {
          rank: 1,
          score: 0.5,
          id: 1,
          doi: null,
          title: "Standing result",
          authors: null,
          abstract: null,
          year: 2020,
          venue: null,
          citation_count: 0,
          is_retracted: false,
          bm25_rank: null,
          vector_rank: null,
          sources: null,
        },
      ],
    });
    const releases: Array<() => void> = [];
    let call = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async () => {
        call += 1;
        if (call > 1) {
          // The second query never resolves during the test: that IS the
          // cold case, a response still in flight.
          await new Promise<void>((r) => releases.push(r));
        }
        return new Response(JSON.stringify(body(5, "matches")), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }),
    );

    const user = userEvent.setup();
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <App />
      </QueryClientProvider>,
    );
    await user.type(screen.getByLabelText("Query"), "anything");
    await user.click(screen.getByRole("button", { name: "Search" }));
    const list = await screen.findByRole("list");
    expect(list).toHaveAttribute("aria-busy", "false");

    await user.click(screen.getByRole("radio", { name: "vector" }));
    // The previous rows must still be MOUNTED — unmounting them is the bug
    // that made every layout animation invisible.
    expect(screen.getByText("Standing result")).toBeInTheDocument();
    // ...and marked as not-current once the fetch outlives the threshold.
    await vi.waitFor(() => expect(screen.getByRole("list")).toHaveAttribute("aria-busy", "true"), {
      timeout: 2000,
    });
    expect(screen.getByText(/searching/i)).toBeInTheDocument();
    releases.forEach((r) => r());
  });
});
