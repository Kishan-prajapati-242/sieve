// The page against a stubbed fetch: what gets POSTed (year bounds only when
// set), and what renders back (took_ms, results, empty state). fetch is
// stubbed rather than msw — one endpoint does not justify a mock server.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SearchResponse } from "./api";
import { App } from "./App";

function renderApp() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>,
  );
}

function stubSearch(response: Partial<SearchResponse>) {
  const full: SearchResponse = {
    query: "",
    mode: "bm25",
    took_ms: 12.3,
    timings: { embed_ms: null, retrieve_ms: 12.0, serialize_ms: 0.3 },
    ef_search: null,
    results: [],
    ...response,
  };
  const spy = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(full), {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", spy);
  return spy;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("App", () => {
  it("submits the query with year bounds only when they are set", async () => {
    const spy = stubSearch({});
    renderApp();
    await userEvent.type(screen.getByLabelText("Query"), "text simplification");
    await userEvent.type(screen.getByLabelText("Year from"), "2020");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));

    expect(spy).toHaveBeenCalledOnce();
    const body = JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string);
    expect(body).toEqual({
      query: "text simplification",
      mode: "bm25",
      k: 20,
      year_from: 2020,
    });
    expect(await screen.findByText(/0 results · 12\.3 ms/)).toBeInTheDocument();
    expect(screen.getByText("No papers matched.")).toBeInTheDocument();
  });

  it("renders returned papers with the query's took_ms", async () => {
    stubSearch({
      took_ms: 58.1,
      results: [
        {
          rank: 1,
          score: 0.9,
          id: 7,
          doi: null,
          title: "A found paper",
          authors: null,
          abstract: null,
          year: 2021,
          venue: null,
          citation_count: 5,
          is_retracted: false,
        },
      ],
    });
    renderApp();
    await userEvent.type(screen.getByLabelText("Query"), "anything");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));

    expect(await screen.findByText("A found paper")).toBeInTheDocument();
    expect(screen.getByText(/1 results · 58\.1 ms/)).toBeInTheDocument();
  });

  it("does not fire a request while typing, only on submit", async () => {
    const spy = stubSearch({});
    renderApp();
    await userEvent.type(screen.getByLabelText("Query"), "slow fingers");
    expect(spy).not.toHaveBeenCalled();
  });

  it("surfaces API failures instead of an empty page", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("boom", { status: 500 })));
    renderApp();
    await userEvent.type(screen.getByLabelText("Query"), "anything");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("HTTP 500");
  });
});
