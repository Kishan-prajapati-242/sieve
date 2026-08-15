// The page against a stubbed fetch: what gets POSTed (year bounds only when
// set), and what renders back (took_ms, results, empty state). fetch is
// stubbed rather than msw — one endpoint does not justify a mock server.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SearchResponse } from "./api";
import { SearchPage } from "./SearchPage";
import { AuthProvider } from "./auth";
import { MemoryRouter } from "react-router-dom";

// The search UI moved to /search when the landing page took the root, so
// these render the route directly rather than the whole App shell — they are
// about the search page's behaviour, not about routing.
function renderApp() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <MemoryRouter initialEntries={["/search"]}>
          <SearchPage />
        </MemoryRouter>
      </AuthProvider>
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
    total: { value: 0, kind: "matches" as const },
    results: [],
    ...response,
  };
  // AuthProvider asks who the caller is through this same stub, so the mock
  // answers both routes and the assertions below filter to /api/search
  // rather than counting every call.
  const spy = vi.fn(async (url: string, _init?: RequestInit) =>
    String(url).includes("/api/stats")
      ? new Response(JSON.stringify({ papers: 183167, retracted_papers: 0, source_records: 0 }), {
          status: 200,
          headers: { "content-type": "application/json" },
        })
      : String(url).includes("/api/auth/me")
      ? new Response(JSON.stringify({ id: 1, email: "reviewer@example.com" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        })
      : new Response(JSON.stringify(full), {
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

    const searches = spy.mock.calls.filter((c) => String(c[0]).includes("/api/search"));
    expect(searches).toHaveLength(1);
    const body = JSON.parse((searches[0][1] as RequestInit).body as string);
    expect(body).toEqual({
      query: "text simplification",
      mode: "hybrid", // the UI default
      k: 20,
      year_from: 2020,
    });
    // The results line now names WHAT the total counts, per mode, and the
    // timings moved to their own breakdown (2026-08-14).
    expect(await screen.findByText(/shown of/)).toBeInTheDocument();
    expect(screen.getByText(/12\.3 ms/)).toBeInTheDocument();
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
    bm25_rank: null,
    vector_rank: null,
    sources: null,
        },
      ],
    });
    renderApp();
    await userEvent.type(screen.getByLabelText("Query"), "anything");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));

    expect(await screen.findByText("A found paper")).toBeInTheDocument();
    expect(screen.getByText(/58\.1 ms/)).toBeInTheDocument();
    expect(screen.getByText(/keyword matches/)).toBeInTheDocument();
  });

  it("posts the mode chosen in the toggle", async () => {
    const spy = stubSearch({});
    renderApp();
    await userEvent.type(screen.getByLabelText("Query"), "anything");
    await userEvent.click(screen.getByRole("radio", { name: "bm25" }));
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    const searches = spy.mock.calls.filter((c) => String(c[0]).includes("/api/search"));
    const body = JSON.parse((searches[0][1] as RequestInit).body as string);
    expect(body.mode).toBe("bm25");
  });

  it("does not fire a request while typing, only on submit", async () => {
    const spy = stubSearch({});
    renderApp();
    await userEvent.type(screen.getByLabelText("Query"), "slow fingers");
    expect(spy.mock.calls.filter((c) => String(c[0]).includes("/api/search"))).toHaveLength(0);
  });

  it("surfaces API failures instead of an empty page", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("boom", { status: 500 })));
    renderApp();
    await userEvent.type(screen.getByLabelText("Query"), "anything");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("HTTP 500");
  });
});
