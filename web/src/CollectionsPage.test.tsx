// View A. Asserts behaviour and visible text only — no class names, which is
// what keeps a restyle from breaking the suite.
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { CollectionsPage } from "./CollectionsPage";
import { renderWith, stubRoutes } from "./testutils";

const ONE = [{
  id: 1, name: "Clinical simplification", question: "Which methods?",
  created_at: "2026-08-13T00:00:00Z", screened: 3, included: 2, excluded: 1, maybe: 0,
}];

afterEach(() => vi.unstubAllGlobals());

it("lists collections with their decision counts", async () => {
  stubRoutes({ "GET /api/collections": ONE });
  renderWith(<CollectionsPage />);
  expect(await screen.findByText("Clinical simplification")).toBeInTheDocument();
  expect(screen.getByText("Which methods?")).toBeInTheDocument();
  expect(screen.getByText("screened")).toBeInTheDocument();
  expect(screen.getByText("3")).toBeInTheDocument();
  expect(screen.getByText("2")).toBeInTheDocument();
});

it("shows an empty state that explains what a collection is", async () => {
  stubRoutes({ "GET /api/collections": [] });
  renderWith(<CollectionsPage />);
  expect(await screen.findByText(/A collection is one literature question/)).toBeInTheDocument();
});

it("posts a new collection and clears the form", async () => {
  const spy = stubRoutes({ "GET /api/collections": [], "POST /api/collections": ONE[0] });
  renderWith(<CollectionsPage />);
  await screen.findByText(/No collections yet/);
  await userEvent.type(screen.getByLabelText("Name"), "New question");
  await userEvent.click(screen.getByRole("button", { name: "New collection" }));
  await waitFor(() => {
    const post = spy.mock.calls.find((c) => (c[1] as RequestInit)?.method === "POST");
    expect(post).toBeTruthy();
    expect(JSON.parse((post![1] as RequestInit).body as string)).toEqual({
      name: "New question", question: null,
    });
  });
  await waitFor(() => expect(screen.getByLabelText("Name")).toHaveValue(""));
});

it("surfaces a create failure without losing the page", async () => {
  vi.stubGlobal("fetch", vi.fn(async (_u: string, init?: RequestInit) =>
    init?.method === "POST"
      ? new Response(JSON.stringify({ detail: "name too long" }), { status: 422 })
      : new Response("[]", { status: 200, headers: { "content-type": "application/json" } })));
  renderWith(<CollectionsPage />);
  await screen.findByText(/No collections yet/);
  await userEvent.type(screen.getByLabelText("Name"), "x");
  await userEvent.click(screen.getByRole("button", { name: "New collection" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("name too long");
});
