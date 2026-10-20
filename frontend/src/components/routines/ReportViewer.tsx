import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  Layers,
  Maximize2,
  Minimize2,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { ReportFrame } from "@/components/routines/ReportFrame";
import { IconButton } from "@/components/ui/IconButton";
import { InlineConfirm } from "@/components/ui/InlineConfirm";
import { FULLSCREEN_LAYER } from "@/components/ui/dialog";
import { SelectField } from "@/components/ui/select-field";
import type { ReportSummary } from "@/lib/api/reports";

interface ReportViewerProps {
  report: ReportSummary;
  reports: ReportSummary[];
  onSelect: (report: ReportSummary) => void;
  onClose?: () => void;
  onDelete?: (id: string) => void;
  allowFullscreen?: boolean;
  /** All source names for cross-routine navigation */
  sourceNames?: string[];
  onSourceChange?: (sourceName: string) => void;
}

export function ReportViewer({
  report,
  reports,
  onSelect,
  onClose,
  onDelete,
  allowFullscreen = true,
  sourceNames,
  onSourceChange,
}: ReportViewerProps) {
  const [fullscreen, setFullscreen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const currentIndex = reports.findIndex((r) => r.id === report.id);
  const hasPrev = currentIndex > 0;
  const hasNext = currentIndex < reports.length - 1;

  const goPrev = useCallback(() => {
    if (hasPrev) onSelect(reports[currentIndex - 1]);
  }, [hasPrev, currentIndex, reports, onSelect]);

  const goNext = useCallback(() => {
    if (hasNext) onSelect(reports[currentIndex + 1]);
  }, [hasNext, currentIndex, reports, onSelect]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement) return;
      if (e.key === "ArrowLeft") { goPrev(); e.preventDefault(); }
      else if (e.key === "ArrowRight") { goNext(); e.preventDefault(); }
      else if (e.key === "Escape" && fullscreen) { setFullscreen(false); e.preventDefault(); }
      // Out of the viewer altogether when it is not fullscreen: with the Back
      // button, this is how a reader leaves a report opened over a view.
      else if (e.key === "Escape" && onClose) { onClose(); e.preventDefault(); }
      else if (e.key === "f" && allowFullscreen && !e.metaKey && !e.ctrlKey) { setFullscreen((f) => !f); }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [goPrev, goNext, fullscreen, allowFullscreen, onClose]);

  return (
    <div
      className={`@container/report-viewer min-h-0 min-w-0 flex flex-col overflow-hidden ${
        fullscreen
          ? FULLSCREEN_LAYER
          : "flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]"
      }`}
    >
      {/* Header */}
      <div className="flex shrink-0 items-center justify-between gap-3 border-b border-[var(--color-border)] px-4 py-2.5 @max-[639px]/report-viewer:flex-wrap @max-[639px]/report-viewer:px-3 @max-[639px]/report-viewer:py-2">
        <div className="min-w-0 flex-1 @max-[639px]/report-viewer:basis-full">
          <h2 className="truncate text-sm font-semibold text-[var(--color-text)]">
            {report.title}
          </h2>
          <div className="flex flex-wrap items-center gap-x-2 text-[10px] text-[var(--color-text-muted)]">
            <span>{new Date(report.created_at).toLocaleString()}</span>
            {report.source_name && (
              <span className="min-w-0 break-words">{report.source_type}: {report.source_name}</span>
            )}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1 @max-[639px]/report-viewer:w-full @max-[639px]/report-viewer:flex-wrap [&>span]:whitespace-nowrap">
          {/* Source switcher */}
          {sourceNames && sourceNames.length > 1 && onSourceChange && (
            <div className="mr-2 flex min-w-0 items-center gap-1">
              <Layers className="h-3 w-3 text-[var(--color-text-muted)]" />
              <SelectField
                size="compact"
                fit
                label="Report source"
                className="max-w-48"
                value={report.source_name}
                onChange={onSourceChange}
                options={sourceNames.map((name) => ({ value: name, label: name.replace(/_/g, " ") }))}
              />
            </div>
          )}
          {/* Navigation counter */}
          {reports.length > 1 && (
            <span className="mr-2 text-[10px] text-[var(--color-text-muted)]">
              {currentIndex + 1} of {reports.length}
            </span>
          )}
          {/* Nav buttons */}
          <IconButton
            label="Previous (Left Arrow)"
            onClick={goPrev}
            disabled={!hasPrev}
          >
            <ChevronLeft className="h-4 w-4" />
          </IconButton>
          <IconButton
            label="Next (Right Arrow)"
            onClick={goNext}
            disabled={!hasNext}
          >
            <ChevronRight className="h-4 w-4" />
          </IconButton>
          {allowFullscreen && (
            <IconButton
              label={fullscreen ? "Exit fullscreen (Esc)" : "Fullscreen (f)"}
              onClick={() => setFullscreen((f) => !f)}
            >
              {fullscreen ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
            </IconButton>
          )}
          {onDelete && (
            <InlineConfirm
              confirming={confirmDelete}
              onRequest={() => setConfirmDelete(true)}
              onConfirm={() => {
                onDelete(report.id);
                setConfirmDelete(false);
              }}
              onCancel={() => setConfirmDelete(false)}
              triggerLabel="Delete report"
              confirmLabel="Confirm delete report"
              cancelLabel="Cancel delete report"
              size="md"
            />
          )}
          {/* Whenever there is somewhere to go back to — not only in
              fullscreen: a viewer opened over a view with fullscreen off had
              no way out at all (CORR-422). */}
          {onClose && (
            <IconButton
              label="Close report"
              title="Back (Esc)"
              data-report-close
              onClick={onClose}
            >
              <ArrowLeft className="h-4 w-4" />
            </IconButton>
          )}
        </div>
      </div>

      {/* iframe */}
      <div className="relative min-h-0 min-w-0 flex-1">
        <ReportFrame reportId={report.id} title={report.title} />
        {/* Fullscreen chevron overlays */}
        {fullscreen && hasPrev && (
          <button
            onClick={goPrev}
            className="@max-[639px]/routine-browser:hidden @max-[639px]/report-viewer:hidden absolute left-2 top-1/2 -translate-y-1/2 rounded-full bg-black/50 p-2 text-white/70 hover:bg-black/70 hover:text-white transition-all"
            title="Previous report"
            aria-label="Previous report"
          >
            <ChevronLeft className="h-5 w-5" />
          </button>
        )}
        {fullscreen && hasNext && (
          <button
            onClick={goNext}
            className="@max-[639px]/routine-browser:hidden @max-[639px]/report-viewer:hidden absolute right-2 top-1/2 -translate-y-1/2 rounded-full bg-black/50 p-2 text-white/70 hover:bg-black/70 hover:text-white transition-all"
            title="Next report"
            aria-label="Next report"
          >
            <ChevronRight className="h-5 w-5" />
          </button>
        )}
      </div>
    </div>
  );
}
