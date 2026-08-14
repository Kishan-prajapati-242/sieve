// View B — the screening surface. The `maybe` filter is tested because a
// maybe exists to be revisited, which is why the fuller view was built.
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { Route, Routes } from "react-router-dom";
import { CollectionPage } from "./CollectionPage";
import { renderWith, stubRoutes } from "./testutils";

const PAPER = {
  id: 42, doi: "10.1/x", title: "Patient-friendly discharge summaries",
  abstract: null, year: 2023, venue: "ACL", citation_count: 7,
  is_retracted: false, authors: ["Ada Lovelace"], arxiv_id: null, pubmed_id: null,
  decision: "maybe" as const, note: "revisit after the protocol", decided_at: "2026-08-13T00:00:00Z",
};
const DETAIL = {
  id: 1, name: "Clinical simplification", question: "Which methods?",
  created_at: "2026-08-13T00:00:00Z", papers: [PAPER],
};

function renderPage() {
  return renderWith(
    <Routes>
      <Route path="/collections/:id" element={<CollectionPage />} />
    </Routes>,
    "/collections/1",
  );
}

afterEach(() => vi.unstubAllGlobals());

it("renders the collection, its papers, and the reviewer's note", async () => {
  stubRoutes({ "GET /api/collections/1": DETAIL });
  renderPage();
  expect(await screen.findByText("Patient-friendly discharge summaries")).toBeInTheDocument();
  expect(screen.getByText("Clinical simplification")).toBeInTheDocument();
  expect(screen.getByText("revisit after the protocol")).toBeInTheDocument();
});

it("marks the current decision as pressed so it is readable without colour", async () => {
  stubRoutes({ "GET /api/collections/1": DETAIL });
  renderPage();
  expect(await screen.findByRole("button", { name: "Maybe", pressed: true })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Include", pressed: false })).toBeInTheDocument();
});

it("PUTs a changed decision to the upsert endpoint", async () => {
  const spy = stubRoutes({ "GET /api/collections/1": DETAIL, "PUT /api/collections/1/screenings/42": {} });
  renderPage();
  await userEvent.click(await screen.findByRole("button", { name: "Include" }));
  await waitFor(() => {
    const put = spy.mock.calls.find((c) => (c[1] as RequestInit)?.method === "PUT");
    expect(put).toBeTruthy();
    expect(put![0]).toContain("/api/collections/1/screenings/42");
    expect(JSON.parse((put![1] as RequestInit).body as string).decision).toBe("include");
  });
});

it("DELETEs on unscreen", async () => {
  const spy = stubRoutes({ "GET /api/collections/1": DETAIL, "DELETE /api/collections/1/screenings/42": {} });
  renderPage();
  await userEvent.click(await screen.findByRole("button", { name: /Remove Patient-friendly/ }));
  await waitFor(() =>
    expect(spy.mock.calls.some((c) => (c[1] as RequestInit)?.method === "DELETE")).toBe(true));
});

it("filters by decision", async () => {
  const spy = stubRoutes({ "GET /api/collections/1": DETAIL });
  renderPage();
  await screen.findByText("Patient-friendly discharge summaries");
  await userEvent.click(screen.getByRole("button", { name: "maybe" }));
  await waitFor(() =>
    expect(spy.mock.calls.some((c) => String(c[0]).includes("decision=maybe"))).toBe(true));
});

it("explains the empty state instead of showing a bare list", async () => {
  stubRoutes({ "GET /api/collections/1": { ...DETAIL, papers: [] } });
  renderPage();
  expect(await screen.findByText(/Search, then add papers from the results/)).toBeInTheDocument();
});

it("offers a BibTeX export link rather than a fetch", async () => {
  stubRoutes({ "GET /api/collections/1": DETAIL });
  renderPage();
  const link = await screen.findByRole("link", { name: "Export .bib" });
  expect(link).toHaveAttribute("href", expect.stringContaining("export.bib"));
});
