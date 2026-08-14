// Shared harness. Every view needs a QueryClient and a router; duplicating
// that in four files is how the setup drifts between them.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

export function renderWith(ui: ReactElement, route = "/") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // The `wrapper` option rather than wrapping `ui` inline: RTL's rerender()
  // re-renders only the element it was given, so an inline wrapper is lost on
  // the second render and the providers vanish.
  function Providers({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[route]}>{children}</MemoryRouter>
      </QueryClientProvider>
    );
  }
  return render(ui, { wrapper: Providers });
}

/** Route fetch by URL+method so a test can stub several endpoints at once —
 *  the collection views hit three. */
export function stubRoutes(routes: Record<string, unknown>) {
  const spy = vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${url.split("?")[0]}`;
    const body = routes[key];
    if (body === undefined) return new Response("null", { status: 404 });
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", spy);
  return spy;
}
