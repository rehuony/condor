import { beforeEach, expect, it, vi } from "vitest";

import { api, type ReportSummary } from "@/lib/api";
import { deleteAllRoutineReports } from "@/components/routines/deleteRoutineReports";

vi.mock("@/lib/api", () => ({ api: { getRoutineReports: vi.fn(), deleteReport: vi.fn() } }));

const report = (id: string) => ({ id }) as ReportSummary;
let stored: string[];

beforeEach(() => {
  vi.mocked(api.getRoutineReports).mockImplementation(async () => ({
    reports: stored.slice(0, 2).map(report), total: stored.length,
  }));
});

it("re-reads the paged listing until every report is gone", async () => {
  stored = ["a", "b", "c", "d", "e"];
  vi.mocked(api.deleteReport).mockImplementation(async (id) => {
    stored = stored.filter((item) => item !== id);
    return { deleted: true };
  });
  const progress = vi.fn();
  expect(await deleteAllRoutineReports("routine", progress)).toEqual({ deleted: 5, failed: 0 });
  expect(stored).toEqual([]);
  expect(progress).toHaveBeenLastCalledWith(5, 5);
});

it("stops when a pass deletes nothing and reports the failures", async () => {
  stored = ["a", "locked"];
  vi.mocked(api.deleteReport).mockImplementation(async (id) => {
    if (id === "locked") throw new Error("Not your report");
    stored = stored.filter((item) => item !== id);
    return { deleted: true };
  });
  expect(await deleteAllRoutineReports("routine")).toEqual({ deleted: 1, failed: 1 });
  expect(stored).toEqual(["locked"]);
});
