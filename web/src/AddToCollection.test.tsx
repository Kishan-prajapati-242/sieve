// The control that closes Phase 3's "screening workflow usable end to end".
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { AddToCollection } from "./AddToCollection";
import { renderWith, stubRoutes } from "./testutils";

const COLLECTIONS = [{
  id: 7, name: "Clinical simplification", question: null,
  created_at: "2026-08-13T00:00:00Z", screened: 0, included: 0, excluded: 0, maybe: 0,
}];

afterEach(() => vi.unstubAllGlobals());

it("screens a paper into a collection straight from the results", async () => {
  const spy = stubRoutes({
    "GET /api/collections": COLLECTIONS,
    "PUT /api/collections/7/screenings/42": {},
  });
  renderWith(<AddToCollection paperId={42} />);
  await userEvent.click(screen.getByRole("button", { name: "Add to…" }));
  await userEvent.click(await screen.findByRole("button", { name: "include" }));
  await waitFor(() => {
    const put = spy.mock.calls.find((c) => (c[1] as RequestInit)?.method === "PUT");
    expect(put).toBeTruthy();
    expect(put![0]).toContain("/api/collections/7/screenings/42");
  });
  expect(await screen.findByRole("button", { name: /include → Clinical simplification/ }))
    .toBeInTheDocument();
});

it("tells the reviewer when there is nowhere to file a paper yet", async () => {
  stubRoutes({ "GET /api/collections": [] });
  renderWith(<AddToCollection paperId={42} />);
  await userEvent.click(screen.getByRole("button", { name: "Add to…" }));
  expect(await screen.findByText(/No collections yet/)).toBeInTheDocument();
});
