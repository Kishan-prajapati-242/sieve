// The assertion class frames found: a count and the rows it describes must
// derive from ONE state. Stepped, not sampled — presentation is a value the
// choreography drives, so these need no timers and cannot go flaky.
import { describe, expect, it } from "vitest";
import { headerCount, presentationSteps } from "./presentation";

// The real bm25 -> hybrid transition at k=20, measured 2026-08-14:
// 5 survivors, 15 arrivals, 0 departures.
const SURVIVORS = [101, 102, 103, 104, 105];
const ARRIVALS = Array.from({ length: 15 }, (_, i) => 200 + i);
const FINAL = [201, 101, 203, 102, 205, 103, 207, 104, 209, 105, 210, 211, 212, 213, 214, 200, 202, 204, 206, 208];

describe("presentation", () => {
  it("never claims more rows than are on screen, at any step", () => {
    const steps = presentationSteps(SURVIVORS, ARRIVALS, FINAL);
    for (const step of steps) {
      // The invariant. The old header failed this for ~600ms mid-transition
      // by reading 20 while 3 rows were visible.
      expect(headerCount(step)).toBe(step.presentedIds.length);
      expect(step.presentedIds.length).toBeLessThanOrEqual(FINAL.length);
      // And every presented id is really in the final list — no phantom rows.
      for (const id of step.presentedIds) expect(FINAL).toContain(id);
    }
  });

  it("never shows fewer rows than it started with", () => {
    // The collapse bug: gating arrivals behind the move made the list drop
    // from 5 rows to 3 and sit nearly empty before expanding to 20. The
    // count is monotonic now — the reader never watches results disappear.
    const counts = presentationSteps(SURVIVORS, ARRIVALS, FINAL).map(headerCount);
    expect(counts[0]).toBe(SURVIVORS.length);
    for (let i = 1; i < counts.length; i++) {
      expect(counts[i]).toBeGreaterThanOrEqual(counts[i - 1]);
    }
    expect(counts.at(-1)).toBe(FINAL.length);
  });

  it("keeps presented rows in final list order the whole way", () => {
    // Arrivals land bottom-up, but a row that is present must sit where it
    // will finally sit — otherwise rows shuffle twice and the climb to #1
    // is illegible.
    for (const step of presentationSteps(SURVIVORS, ARRIVALS, FINAL)) {
      const expected = FINAL.filter((id) => step.presentedIds.includes(id));
      expect(step.presentedIds).toEqual(expected);
    }
  });

  it("presents survivors immediately rather than hiding them", () => {
    const [first] = presentationSteps(SURVIVORS, ARRIVALS, FINAL);
    expect(first.phase).toBe("moving");
    expect(first.presentedIds.length).toBe(SURVIVORS.length);
  });

  it("handles a total replacement, where there is nothing to move", () => {
    // bm25 -> vector: 20 arriving, 0 staying. No move phase at all.
    const arrivals = Array.from({ length: 20 }, (_, i) => 300 + i);
    const steps = presentationSteps([], arrivals, arrivals);
    expect(steps[0].phase).toBe("arriving");
    expect(headerCount(steps[0])).toBe(1);
    expect(steps.map(headerCount)).toEqual([...arrivals.map((_, i) => i + 1), 20]);
  });

  it("handles an unchanged result set", () => {
    const steps = presentationSteps(SURVIVORS, [], SURVIVORS);
    expect(steps.every((s) => headerCount(s) === SURVIVORS.length)).toBe(true);
  });
});
