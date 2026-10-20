import { reportsApi } from "@/lib/api/reports";
import { routinesApi } from "@/lib/api/routines";

export interface DeleteAllResult {
  deleted: number;
  failed: number;
}

/**
 * Delete every report a routine has produced that the caller can see.
 *
 * The listing is paged, so it is re-read until it comes back empty. A pass
 * that deletes nothing stops the loop, leaving the failures for the caller.
 */
export async function deleteAllRoutineReports(
  source: string,
  onProgress?: (deleted: number, total: number) => void,
): Promise<DeleteAllResult> {
  let deleted = 0;
  let failed = 0;
  let total: number | null = null;
  const skipped = new Set<string>();
  for (;;) {
    const page = await routinesApi.getRoutineReports(source);
    total ??= page.total;
    const pending = page.reports.filter((report) => !skipped.has(report.id));
    if (pending.length === 0) break;
    for (const report of pending) {
      try {
        await reportsApi.deleteReport(report.id);
        deleted += 1;
      } catch {
        skipped.add(report.id);
        failed += 1;
      }
      onProgress?.(deleted, Math.max(total, deleted + failed));
    }
  }
  return { deleted, failed };
}
