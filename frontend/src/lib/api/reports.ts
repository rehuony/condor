/** Report listing, content and deletion. */

import { authFetch } from "../auth-token";
import { apiFetch, query } from "@/lib/api";

// ── Reports ──

export interface ReportSummary {
  id: string;
  title: string;
  filename: string;
  created_at: string;
  source_type: string;
  source_name: string;
  tags: string[];
  /** Producing assistant — "" on reports written before attribution existed. */
  agent?: string;
}

export interface ReportsListResponse {
  reports: ReportSummary[];
  total: number;
}

export interface ReportGroup {
  source_name: string;
  source_type: string;
  latest_report: ReportSummary;
  total_count: number;
  all_tags: string[];
}

export const reportsApi = {
  // ── Reports ──

  getReports: (params?: {
    source_type?: string;
    tag?: string;
    search?: string;
    agent?: string;
    limit?: number;
    offset?: number;
  }) => {
    return apiFetch<ReportsListResponse>(
      `/api/v1/reports${query(params ?? {})}`,
    );
  },

  getReport: (id: string) =>
    apiFetch<ReportSummary>(`/api/v1/reports/${encodeURIComponent(id)}`),

  getReportsGrouped: () =>
    apiFetch<ReportGroup[]>("/api/v1/reports/latest-by-source"),

  /**
   * A report's rendered HTML body.
   *
   * Report bodies are authenticated (SEC-112), and an iframe `src` cannot carry
   * an Authorization header — so callers fetch the HTML here and hand it to the
   * iframe as `srcDoc`, keeping the token in a header instead of the URL.
   *
   * A chart report does not carry plotly.js; it references the one shared copy
   * at `/api/v1/reports/assets/` (PERF-267), which a `srcdoc` frame resolves
   * against the parent origin and the browser caches across reports. Pass
   * `hydrate` to get that bundle inlined instead — for the Download button,
   * whose file is opened at `file://` where nothing resolves.
   */
  getReportHtml: async (
    id: string,
    options: { hydrate?: boolean } = {},
  ): Promise<string> => {
    const res = await authFetch(
      `/api/v1/reports/${encodeURIComponent(id)}/html${options.hydrate ? "?hydrate=1" : ""}`,
    );
    if (!res.ok) throw new Error(`Failed to load report (${res.status})`);
    return res.text();
  },

  deleteReport: (id: string) =>
    apiFetch<{ deleted: boolean }>(
      `/api/v1/reports/${encodeURIComponent(id)}`,
      { method: "DELETE" },
    ),
};
