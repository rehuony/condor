import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bell,
  Brain,
  ChevronLeft,
  ChevronRight,
  Clock,
  Download,
  ListX,
  Play,
  PlayCircle,
  Settings2,
  Trash2,
  X,
  Zap,
  AlertTriangle,
  Code,
  MoreHorizontal,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AnchoredMenu } from "@/components/ui/AnchoredMenu";
import { IconButton } from "@/components/ui/IconButton";
import { Spinner } from "@/components/ui/Spinner";
import { FULLSCREEN_LAYER, FULLSCREEN_LAYER_TOP } from "@/components/ui/dialog";
import { SelectField } from "@/components/ui/select-field";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { reportsApi } from "@/lib/api/reports";
import { routinesApi, type RoutineInstance } from "@/lib/api/routines";
import { toMs } from "@/lib/formatters";
import {
  buildConfigValues,
  formatAgo,
  formatInterval,
  formatRoutineName,
  formatSlug,
  inScope,
  invalidateRoutineQueries,
  resolveRoutine,
  saveConfig,
} from "@/lib/routineUtils";
import { useViewFacts } from "@/lib/viewFacts";
import { useServer } from "@/hooks/useServer";
import { ReportFrame } from "./ReportFrame";
import { RoutinePicker } from "./RoutinePicker";
import {
  RoutineLibrarySidebar,
  type RoutineReportInfo,
} from "./RoutineLibrarySidebar";
import { RoutineConfigForm } from "./RoutineConfigForm";
import { RoutineHooksPanel } from "./RoutineHooksPanel";
import { RoutineResultView } from "./RoutineResultView";
import { ScheduleDropdown } from "./ScheduleDropdown";
import { ROUTINE_ACTION, ROUTINE_PRIMARY_ACTION } from "./routineActionClass";

/**
 * The conversation a run launched from here belongs to.
 *
 * Without one — the `/routines` page, an agent's own page — a run is what it
 * has always been: the dashboard's server, no conversation behind it.
 */
export interface RoutineRunContext {
  /** The server the conversation is talking to, which is where its runs go. */
  serverName: string;
  /** Resolved server-side into the conversation the run reports back to. */
  sessionKey: string;
  /** Who the run's reports are filed under, as the chat's own runs are. */
  agentSlug?: string;
}

interface ReportBrowserProps {
  initialSource?: string;
  /** Open on this report of {@link initialSource}, rather than its latest. */
  initialReportId?: string;
  /**
   * Open on this run of {@link initialSource}. A run that wrote no report opens
   * on its output, which is the whole of what it produced.
   */
  initialInstanceId?: string;
  initialSourceTypeFilter?: string;
  instances: RoutineInstance[];
  /** Absent on the `/routines` page: a nav destination has nothing to close. */
  onClose?: () => void;
  /**
   * Rendered inside the workspace pane: fill the container instead of the
   * viewport, and leave the window's keys and the Close button to the host.
   */
  hosted?: boolean;
  /**
   * Rendered as the `/routines` page: fill the shell's main area rather than
   * the viewport, keep the window's keys — the page *is* the screen — and drop
   * the Close button, since there is nothing behind it to go back to.
   */
  page?: boolean;
  /**
   * The host owns the picking: it lists the routines and filters them, so this
   * pane drops its own sidebar, scope select and title and is nothing but the
   * report and what you can do to it. The host asks both questions with the
   * same {@link RoutinePicker} this header asks the scope half of.
   */
  externalPicker?: boolean;
  /** Told whenever the pane moves to another routine — its own ↑/↓ still do. */
  onSourceChange?: (name: string) => void;
  /** Run and schedule as this conversation, on its server. */
  runContext?: RoutineRunContext;
}

export function ReportBrowser({
  instances,
  initialSource,
  initialReportId,
  initialInstanceId,
  initialSourceTypeFilter,
  onClose,
  hosted = false,
  page = false,
  externalPicker = false,
  onSourceChange,
  runContext,
}: ReportBrowserProps) {
  const { server } = useServer();
  // The conversation's server wins over the dashboard's selector: a routine
  // asked for beside a chat runs where that chat is pointed, not where a page
  // the reader last touched happens to point.
  const runServer = runContext?.serverName || server;
  const qc = useQueryClient();
  // The /routines page still filters in the older vocabulary, where the
  // general library is `"routine"`; the picker's select only has an option for
  // `"condor"`, and a native select handed a value no option carries shows the
  // first one while the state says otherwise. Normalised on the way in, so the
  // alias stops at this boundary rather than reaching the control.
  const [sourceTypeFilter, setSourceTypeFilter] = useState<string>(
    initialSourceTypeFilter === "routine"
      ? "condor"
      : initialSourceTypeFilter || "all",
  );
  const [descriptionOpen, setDescriptionOpen] = useState(false);
  // 550px of pane cannot hold a 256px list and a report: hosted opens on the
  // 48px rail, the page keeps its list, and the toggle still works either way.
  const [isCompact, setIsCompact] = useState(hosted);
  // Typed into the sidebar's own box, which only the expanded list has — the
  // rail and the host-picked pane never narrow the list this way.
  const [search, setSearch] = useState("");
  const [showConfigPanel, setShowConfigPanel] = useState(false);
  const [showNotifyPanel, setShowNotifyPanel] = useState(false);
  const [showSourceModal, setShowSourceModal] = useState(false);
  const sidebarRef = useRef<HTMLDivElement>(null);
  const setupRef = useRef<HTMLDivElement>(null);

  // Fetch all routines for the sidebar
  const { data: routines = [] } = useQuery({
    queryKey: ["routines"],
    queryFn: routinesApi.getRoutines,
  });

  const [pickedSource, setActiveSource] = useState(initialSource ?? "");
  /**
   * Reports, or the run's own text.
   *
   * A run that wrote a report is read as that report; one that only answered in
   * text — most one-shots — has nothing in the report index, and its output is
   * the whole of what it produced. Both are the same routine, so they are two
   * views of this pane rather than two places to look.
   */
  const [view, setView] = useState<"report" | "output">(
    initialInstanceId && !initialReportId ? "output" : "report",
  );

  /**
   * Picking another routine is always a fresh read of it.
   *
   * With a host picker, the pick is the host's to make: it holds which routine
   * the library is on — for its own list as much as for this pane — and hands
   * it back as `initialSource`.
   */
  const selectSource = useCallback(
    (name: string) => {
      if (onSourceChange) {
        onSourceChange(name);
        return;
      }
      setActiveSource(name);
      setView("report");
    },
    [onSourceChange],
  );

  /**
   * The routine on screen, said the way the library says it.
   *
   * Two spellings reach here for one routine. A run names it as the store
   * registered it — `{slug}/{name}` for an agent's own — while a report names it
   * as the report index filed it, which is the bare name. Both have to land on
   * the same routine, or the header's Run and Config would act on nothing.
   */
  const activeRoutine = useMemo(
    () => resolveRoutine(routines, pickedSource),
    [routines, pickedSource],
  );
  const activeSource = activeRoutine?.name ?? pickedSource;

  /**
   * The scope the list actually shows.
   *
   * A row opened from a conversation may point at a shared or general-library
   * routine that the agent's own scope excludes, and a list that does not
   * contain what the pane is showing is worse than a wider one — so the pane
   * widens itself rather than hiding what the reader just clicked.
   */
  const effectiveScope =
    activeRoutine &&
    !inScope(routines, sourceTypeFilter).some(
      (r) => r.name === activeRoutine.name,
    )
      ? "all"
      : sourceTypeFilter;

  /**
   * The list, as the sidebar renders it: the picked scope, narrowed by the
   * search box.
   *
   * One list, not two — ↑/↓ and Run All act on exactly what is on screen. A
   * search that hid rows from the list while the arrows still walked them
   * would be a filter the keyboard disagrees with.
   */
  const filteredRoutines = useMemo(() => {
    const scoped = inScope(routines, effectiveScope);
    const q = search.trim().toLowerCase();
    if (!q) return scoped;
    return scoped.filter(
      (r) =>
        r.name.toLowerCase().includes(q) ||
        formatRoutineName(r.name).toLowerCase().includes(q) ||
        r.description.toLowerCase().includes(q) ||
        r.category.toLowerCase().includes(q),
    );
  }, [routines, effectiveScope, search]);

  /**
   * What each routine's latest report says, for the card that names it.
   *
   * Fetched only where the cards are: the dock hands its own picker the list,
   * so nothing there reads this.
   */
  const { data: reportGroups = [] } = useQuery({
    queryKey: ["reports-grouped"],
    queryFn: reportsApi.getReportsGrouped,
    enabled: !externalPicker,
    refetchInterval: 30_000,
  });
  const reportInfo = useMemo(() => {
    const map = new Map<string, RoutineReportInfo>();
    for (const g of reportGroups) {
      map.set(g.source_name, {
        title: g.latest_report.title,
        created_at: g.latest_report.created_at,
        count: g.total_count,
        tags: g.all_tags,
      });
    }
    return map;
  }, [reportGroups]);

  // Set initial source once routines load if not set — pick from filtered list
  useEffect(() => {
    if (!pickedSource && filteredRoutines.length > 0) {
      selectSource(filteredRoutines[0].name);
    }
  }, [pickedSource, filteredRoutines, selectSource]);

  /** Narrowing the list moves the reader into it, rather than stranding them. */
  const changeScope = useCallback(
    (next: string) => {
      setSourceTypeFilter(next);
      const scoped = inScope(routines, next);
      if (!scoped.some((r) => r.name === activeSource)) {
        selectSource(scoped[0]?.name ?? "");
      }
    },
    [routines, activeSource, selectSource],
  );

  // Unique source types for filter
  const hasAgents = routines.some((r) => r.source.startsWith("agent:"));

  /**
   * Who owns the open routine, when an agent does.
   *
   * The header names it only where the scope picker does not already: with the
   * list narrowed to that agent the badge restates the control beside it, but
   * in a mixed list — "All routines", "All agents", or the widening this pane
   * does for a routine outside the picked scope — it is the only thing that
   * says whose routine this is.
   */
  const agentOwner = activeRoutine?.source.startsWith("agent:")
    ? activeRoutine.source.slice("agent:".length)
    : null;

  // Reports for active source — poll when a scheduled instance is active
  const hasScheduledInstance = instances.some(
    (i) => i.routine_name === activeSource && (i.status === "running" || i.status === "scheduled"),
  );
  const { data: reportsData, isLoading: loadingReports } = useQuery({
    queryKey: ["routine-reports", activeSource],
    queryFn: () => routinesApi.getRoutineReports(activeSource),
    enabled: !!activeSource,
    refetchInterval: hasScheduledInstance ? 10_000 : false,
  });
  // Memoized: identity feeds the report that opens first, and a new array on
  // every render would recompute it on every render.
  const reports = useMemo(() => reportsData?.reports ?? [], [reportsData]);

  // Source code query (lazy)
  const { data: sourceData } = useQuery({
    queryKey: ["routine-source", activeSource],
    queryFn: () => routinesApi.getRoutineSource(activeSource),
    enabled: showSourceModal && !!activeSource,
  });

  /**
   * Which report is open: the reader's pick, else the one the pane was opened
   * for.
   *
   * A dock row points at one run, not at the newest, so "no pick yet" resolves
   * to that report rather than to index 0 — derived rather than corrected in an
   * effect, so the right report is on screen in the first paint and a poll that
   * refreshes the list can never drag the reader back to it.
   */
  const [pickedReportIdx, setSelectedReportIdx] = useState<number | null>(null);
  const openedOnIdx = useMemo(() => {
    if (!initialReportId) return 0;
    return Math.max(
      0,
      reports.findIndex((r) => r.id === initialReportId),
    );
  }, [reports, initialReportId]);
  const selectedReportIdx = pickedReportIdx ?? openedOnIdx;
  const selectedReport = reports[selectedReportIdx] ?? null;

  // Another routine is read from the top — back to "no pick", which is the
  // newest for a routine the pane was not opened on.
  useEffect(() => {
    setSelectedReportIdx(null);
  }, [activeSource]);

  // Active instances for current source
  const sourceInstances = useMemo(
    () => instances.filter((i) => i.routine_name === activeSource && (i.status === "running" || i.status === "scheduled")),
    [instances, activeSource],
  );

  /**
   * The run behind the Output view: the one the reader clicked, else this
   * routine's most recent. Runs live in memory, so there may be none — the
   * toggle is offered only when there is something to toggle to.
   */
  const outputRun = useMemo(() => {
    const forSource = instances.filter((i) => i.routine_name === activeSource);
    const clicked =
      initialInstanceId &&
      forSource.find((i) => i.instance_id === initialInstanceId);
    if (clicked) return clicked;
    return (
      [...forSource].sort(
        (a, b) =>
          toMs(b.last_run_at ?? b.created_at) -
          toMs(a.last_run_at ?? a.created_at),
      )[0] ?? null
    );
  }, [instances, activeSource, initialInstanceId]);

  // `last_result` on the list is truncated; the sections, tables and chart of a
  // run live behind its own route, so the view that shows them fetches it.
  const { data: outputRunFull } = useQuery({
    queryKey: ["routine-instance", outputRun?.instance_id],
    queryFn: () => routinesApi.getRoutineInstance(outputRun!.instance_id),
    enabled: view === "output" && !!outputRun,
  });
  const shownRun = outputRunFull ?? outputRun;
  // Offered while there is a run with something to say — and kept while the
  // reader is looking at it, so the switch cannot vanish from under them.
  const hasOutput =
    view === "output" ||
    !!(outputRun && (outputRun.has_result || outputRun.last_result || outputRun.error));

  // Latest failed instance for error display
  const latestFailedInstance = useMemo(
    () => instances.find((i) => i.routine_name === activeSource && i.status === "failed"),
    [instances, activeSource],
  );

  // Config state: merge routine fields with saved localStorage values
  const [configValues, setConfigValues] = useState<Record<string, unknown>>({});

  useEffect(() => {
    if (!activeRoutine) return;
    setConfigValues(buildConfigValues(activeRoutine));
    setShowConfigPanel(false);
  }, [activeSource, activeRoutine]);

  // Track running instance to poll for completion
  const [pollingInstanceId, setPollingInstanceId] = useState<string | null>(null);

  const { data: polledInstance } = useQuery({
    queryKey: ["routine-instance", pollingInstanceId],
    queryFn: () => routinesApi.getRoutineInstance(pollingInstanceId!),
    enabled: !!pollingInstanceId,
    refetchInterval: 2000,
  });

  // When polled instance completes, refresh reports
  useEffect(() => {
    if (polledInstance && polledInstance.status !== "running") {
      setPollingInstanceId(null);
      invalidateRoutineQueries(qc, activeSource);
    }
  }, [polledInstance, activeSource, qc]);

  const runMutation = useMutation({
    mutationFn: () =>
      routinesApi.runRoutine(runServer!, activeSource, configValues, {
        sessionKey: runContext?.sessionKey,
        attributeTo: runContext?.agentSlug,
      }),
    onSuccess: (data) => {
      setPollingInstanceId(data.instance_id);
      qc.invalidateQueries({ queryKey: ["routine-instances"] });
      setShowConfigPanel(false);
    },
  });

  const scheduleMutation = useMutation({
    mutationFn: (intervalSec: number) =>
      routinesApi.scheduleRoutine(runServer!, activeSource, configValues, intervalSec),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["routine-instances"] });
      setShowConfigPanel(false);
    },
  });

  const stopMutation = useMutation({
    mutationFn: (id: string) => routinesApi.stopRoutineInstance(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["routine-instances"] });
    },
  });

  const invalidateReports = useCallback((source: string) => {
    // The routine list carries each routine's report count, which the cards
    // fall back to once a routine has no reports left in the grouped summary.
    qc.invalidateQueries({ queryKey: ["routines"] });
    qc.invalidateQueries({ queryKey: ["reports"] });
    qc.invalidateQueries({ queryKey: ["reports-grouped"] });
    qc.invalidateQueries({ queryKey: ["routine-reports", source] });
  }, [qc]);

  // Freeze the target while the dialog is open; report polling may change the selection.
  const [confirmDelete, setConfirmDelete] = useState<
    | { kind: "one"; source: string; id: string; label: string }
    | { kind: "all"; source: string; total: number }
    | null
  >(null);

  const deleteMutation = useMutation({
    mutationFn: ({ id }: { id: string; source: string }) => reportsApi.deleteReport(id),
    onSuccess: (_, { id, source }) => {
      invalidateReports(source);
      setConfirmDelete(null);
      if (selectedReport?.id === id) {
        setSelectedReportIdx(Math.max(0, selectedReportIdx - 1));
      }
    },
  });

  // One request: the server deletes the whole set under one index write.
  const deleteAllMutation = useMutation({
    mutationFn: (source: string) => routinesApi.deleteRoutineReports(source),
    onSuccess: (_, source) => {
      setConfirmDelete(null);
      if (activeSource === source) setSelectedReportIdx(0);
    },
    onSettled: (_, _error, source) => invalidateReports(source),
  });
  const deleting = deleteMutation.isPending || deleteAllMutation.isPending;
  const closeDeleteDialog = () => {
    if (deleting) return;
    setConfirmDelete(null);
    deleteMutation.reset();
    deleteAllMutation.reset();
  };
  const deleteAllError = deleteAllMutation.error
    ? deleteAllMutation.error instanceof Error ? deleteAllMutation.error.message : "Delete failed."
    : null;

  // Run All state. It acts on every listed routine, so it always asks first.
  const [runAllProgress, setRunAllProgress] = useState<{ current: number; total: number } | null>(null);
  const [confirmRunAll, setConfirmRunAll] = useState(false);

  const runAll = useCallback(async () => {
    if (!runServer || filteredRoutines.length === 0) return;
    const toRun = filteredRoutines;
    setRunAllProgress({ current: 0, total: toRun.length });
    for (let i = 0; i < toRun.length; i++) {
      setRunAllProgress({ current: i + 1, total: toRun.length });
      const routine = toRun[i];
      const cfg = buildConfigValues(routine);
      try {
        await routinesApi.runRoutine(runServer, routine.name, cfg, {
          sessionKey: runContext?.sessionKey,
          attributeTo: runContext?.agentSlug,
        });
      } catch {
        // continue with remaining routines
      }
    }
    setRunAllProgress(null);
    invalidateRoutineQueries(qc);
    qc.invalidateQueries({ queryKey: ["routine-reports"] });
  }, [runServer, runContext, filteredRoutines, qc]);

  /**
   * Download the report body.
   *
   * Report HTML is authenticated (SEC-112), so a bare `href` to it no longer
   * works: fetch it with the token in a header and save the response as a blob.
   */
  const downloadReport = useCallback(async () => {
    if (!selectedReport) return;
    // Hydrated: the saved file is opened at `file://`, where the shared plotly
    // bundle the stored report references cannot resolve (PERF-267).
    const html = await reportsApi.getReportHtml(selectedReport.id, { hydrate: true });
    const url = URL.createObjectURL(new Blob([html], { type: "text/html" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = selectedReport.filename;
    link.click();
    URL.revokeObjectURL(url);
  }, [selectedReport]);

  // Keyboard navigation
  const activeSourceIdx = filteredRoutines.findIndex((r) => r.name === activeSource);

  const goSourceUp = useCallback(() => {
    if (activeSourceIdx > 0) {
      selectSource(filteredRoutines[activeSourceIdx - 1].name);
    }
  }, [activeSourceIdx, filteredRoutines, selectSource]);

  const goSourceDown = useCallback(() => {
    if (activeSourceIdx < filteredRoutines.length - 1) {
      selectSource(filteredRoutines[activeSourceIdx + 1].name);
    }
  }, [activeSourceIdx, filteredRoutines, selectSource]);

  const goPrevReport = useCallback(() => {
    if (selectedReportIdx > 0) setSelectedReportIdx(selectedReportIdx - 1);
  }, [selectedReportIdx, setSelectedReportIdx]);

  const goNextReport = useCallback(() => {
    if (selectedReportIdx < reports.length - 1)
      setSelectedReportIdx(selectedReportIdx + 1);
  }, [selectedReportIdx, reports.length, setSelectedReportIdx]);

  /**
   * The browser's own keys.
   *
   * Full screen they are the window's, because the browser *is* the window.
   * Hosted they are the container's: a live composer sits beside the pane, and
   * a "k" typed into the chat that paged the report list would make the whole
   * arrangement feel broken. Escape is the host's too — {@link WorkspaceSheet}
   * owns closing the pane, and Escape belongs to the conversation — so hosted
   * it only dismisses this browser's own panels.
   */
  const handleKey = useCallback(
    (e: Pick<KeyboardEvent, "key" | "target" | "preventDefault" | "defaultPrevented">) => {
      if (e.defaultPrevented) return;
      if (e.target instanceof Element && e.target.closest('[role="listbox"], [aria-haspopup="listbox"]')) return;
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement) return;
      if (e.key === "ArrowUp") { goSourceUp(); e.preventDefault(); }
      else if (e.key === "ArrowDown") { goSourceDown(); e.preventDefault(); }
      else if (e.key === "ArrowLeft") { goPrevReport(); e.preventDefault(); }
      else if (e.key === "ArrowRight") { goNextReport(); e.preventDefault(); }
      else if (e.key === "Escape") {
        if (showSourceModal) setShowSourceModal(false);
        else if (showConfigPanel) setShowConfigPanel(false);
        else if (showNotifyPanel) setShowNotifyPanel(false);
        else if (!hosted && onClose) onClose();
        else return;
        e.preventDefault();
      }
    },
    [goSourceUp, goSourceDown, goPrevReport, goNextReport, onClose, hosted, showConfigPanel, showNotifyPanel, showSourceModal],
  );

  useEffect(() => {
    if (hosted) return;
    const handler = (e: KeyboardEvent) => handleKey(e);
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [hosted, handleKey]);

  // Hosted, the keys only reach the browser while it has focus — so it takes
  // focus once, on open. The click that opened it already left the composer.
  const rootRef = useRef<HTMLDivElement>(null);
  const [moreAnchor, setMoreAnchor] = useState<HTMLButtonElement | null>(null);
  const [moreOpen, setMoreOpen] = useState(false);
  useEffect(() => {
    if (hosted) rootRef.current?.focus({ preventScroll: true });
  }, [hosted]);

  // Scroll active source into view
  useEffect(() => {
    const el = sidebarRef.current?.querySelector("[data-active-source]");
    el?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [activeSource]);

  // Tell the chat what report is open, for as long as the reader is (FEAT-059).
  useViewFacts(() =>
    view === "output" && shownRun
      ? {
          label: "Routine output",
          subject: `the output of the last ${activeSource} run`,
        }
      : selectedReport && view === "report"
        ? {
            label: "Routine report",
            subject: `report "${selectedReport.title}" (${selectedReport.filename}) from ${activeSource}`,
          }
        : null,
  );

  const emptyReport = view === "report" && !loadingReports && !selectedReport;
  const failedRun = polledInstance?.status === "failed" ? polledInstance : latestFailedInstance;
  const showSetup = emptyReport && !failedRun;
  const configForm = activeRoutine && (
    Object.keys(activeRoutine.fields).length > 0 ? (
      <RoutineConfigForm
        key={activeSource}
        fields={activeRoutine.fields}
        values={configValues}
        onChange={(key, value) => {
          setConfigValues((prev) => {
            const next = { ...prev, [key]: value };
            saveConfig(activeSource, next);
            return next;
          });
        }}
      />
    ) : (
      <p className="text-sm text-[var(--color-text-muted)]">No configurable fields</p>
    )
  );

  const showRunAll = filteredRoutines.length > 1 || !!runAllProgress;
  const runAllButton = (className: string, iconOnly = false) => showRunAll && (
    <button
      type="button"
      onClick={() => setConfirmRunAll(true)}
      disabled={!!runAllProgress || !runServer}
      className={className}
      title="Run all filtered routines with saved configs and defaults"
      aria-label={runAllProgress ? `Running routines ${runAllProgress.current} of ${runAllProgress.total}` : `Run all ${filteredRoutines.length} filtered routines`}
    >
      {runAllProgress ? <Spinner /> : <PlayCircle />}
      {!iconOnly && (
        <span aria-live="polite">{runAllProgress ? `${runAllProgress.current}/${runAllProgress.total}` : "Run All"}</span>
      )}
    </button>
  );

  const secondaryActions = (
    <>
      {activeRoutine && runServer && (
        <>
          <button
            type="button"
            onClick={() => setShowSourceModal(true)}
            className={ROUTINE_ACTION}
            title="View source code"
          >
            <Code /> Source
          </button>
          <button
            type="button"
            onClick={() => {
              setShowNotifyPanel((v) => !v);
              setShowConfigPanel(false);
            }}
            aria-expanded={showNotifyPanel}
            className={ROUTINE_ACTION}
            title="Post-run notifications"
          >
            <Bell /> Notify
          </button>
        </>
      )}
      {/* Run All belongs to the routine list. It is here only where that
          list is not drawn: a host's own picker, or a page below md. */}
      {(externalPicker || page) && runAllButton(`${ROUTINE_ACTION} ${externalPicker ? "" : "md:hidden"}`)}
    </>
  );

  return (
    <div
      ref={rootRef}
      // Hosted, the pane already sizes and paints; full screen, this is the page.
      className={
        hosted
          ? "@container/routine-browser flex min-h-0 w-full min-w-0 flex-1 flex-col md:flex-row overflow-hidden outline-none"
          : page
            ? "@container/routine-browser flex h-full min-h-0 w-full min-w-0 flex-col md:flex-row overflow-hidden outline-none"
            : `@container/routine-browser ${FULLSCREEN_LAYER} min-w-0 md:flex-row`
      }
      tabIndex={hosted ? -1 : undefined}
      onKeyDown={hosted ? handleKey : undefined}
    >
      {/* The library's left column — dropped when the host lists the routines
          itself (the dock's picker), which is what gives the report the whole
          pane. */}
      {!externalPicker && (
        <RoutineLibrarySidebar
          routines={routines}
          listed={filteredRoutines}
          instances={instances}
          activeSource={activeSource}
          onSelect={selectSource}
          scope={effectiveScope}
          onScopeChange={changeScope}
          search={search}
          onSearchChange={setSearch}
          compact={isCompact}
          onToggleCompact={() => setIsCompact((v) => !v)}
          onStopInstance={(id) => stopMutation.mutate(id)}
          stopping={stopMutation.isPending}
          listRef={sidebarRef}
          reportInfo={reportInfo}
          page={page}
          headerAction={runAllButton(
            isCompact
              ? "flex h-7 w-7 items-center justify-center rounded text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)] disabled:opacity-50 [&>svg]:h-4 [&>svg]:w-4"
              : "flex h-7 shrink-0 items-center gap-1.5 rounded-md px-2 text-xs font-medium text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)] disabled:opacity-50 [&>svg]:h-3.5 [&>svg]:w-3.5",
            isCompact,
          )}
        />
      )}

      {/* Main content */}
      <div className={`flex min-h-0 min-w-0 flex-1 flex-col ${page ? "overflow-y-auto overflow-x-hidden" : ""}`}>
        {/* A page scrolls as one document; a hosted pane keeps the report in
            view by capping the controls on narrow widths. */}
        <div className={page ? "block flex-none" : "contents @max-[639px]/routine-browser:block @max-[639px]/routine-browser:max-h-[50%] @max-[639px]/routine-browser:min-h-0 @max-[639px]/routine-browser:shrink-0 @max-[639px]/routine-browser:overflow-y-auto"}>
        {page && !externalPicker && (
          <div data-routine-picker-bar className="block shrink-0 border-b border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 md:hidden @max-[639px]/routine-browser:block">
            {/* One control on narrow screens: the scope is chosen inside the menu. */}
            <RoutinePicker
              parts="routine"
              variant="inline"
              scopeInMenu
              routines={routines}
              instances={instances}
              source={activeSource}
              scope={effectiveScope}
              onScopeChange={changeScope}
              onSelect={selectSource}
            />
          </div>
        )}
        <div className="flex shrink-0 flex-wrap items-start justify-between gap-4 border-b border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-4 md:px-6 @max-[639px]/routine-browser:items-center @max-[639px]/routine-browser:gap-2 @max-[639px]/routine-browser:px-3 @max-[639px]/routine-browser:py-2">
          {(!externalPicker || sourceInstances.length > 0) && (
            <div className="min-w-0 max-w-full flex-1 basis-64 space-y-2 @max-[639px]/routine-browser:basis-full @max-[639px]/routine-browser:space-y-1">
              {!externalPicker && (
                <>
                  {/* The narrow picker bar already names the routine. */}
                  <div className={`flex min-w-0 flex-wrap items-center gap-2 ${page ? "max-md:hidden @max-[639px]/routine-browser:hidden" : ""}`}>
                    {hasAgents && isCompact && (
                      <div className={page ? "hidden md:block" : undefined}>
                        <RoutinePicker
                          parts="scope"
                          variant="inline"
                          routines={routines}
                          scope={effectiveScope}
                          onScopeChange={changeScope}
                          onSelect={selectSource}
                        />
                      </div>
                    )}
                    <h2 className="min-w-0 break-words text-lg font-semibold text-[var(--color-text)] @max-[639px]/routine-browser:[overflow-wrap:anywhere]">
                      {formatRoutineName(activeSource) || "Routines"}
                    </h2>
                    {agentOwner && effectiveScope !== agentOwner && (
                      <span className="flex items-center gap-1 rounded bg-purple-500/10 px-2 py-1 text-[10px] font-semibold uppercase text-purple-400">
                        <Brain className="h-3 w-3" />
                        {formatSlug(agentOwner)}
                      </span>
                    )}
                  </div>
                  {activeRoutine?.description && (
                    // One line on narrow panes; tap to read the rest.
                    <button
                      type="button"
                      aria-expanded={descriptionOpen}
                      onClick={() => setDescriptionOpen((open) => !open)}
                      title={activeRoutine.description}
                      className={`block w-full max-w-3xl cursor-default text-left text-sm leading-relaxed text-[var(--color-text-muted)] @max-[639px]/routine-browser:cursor-pointer @max-[639px]/routine-browser:text-xs ${
                        descriptionOpen ? "" : "@max-[639px]/routine-browser:line-clamp-1"
                      }`}
                    >
                      {activeRoutine.description}
                    </button>
                  )}
                </>
              )}
              {sourceInstances.length > 0 && (
                <div className="flex flex-wrap items-center gap-2">
                  {sourceInstances.map((inst) => (
                    <div key={inst.instance_id} className="flex items-center gap-1.5 rounded-full bg-emerald-500/10 px-2.5 py-1 text-[10px]">
                      <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
                      <span className="text-emerald-400 capitalize">{inst.status}</span>
                      {inst.schedule?.type === "interval" && (
                        <span className="text-[var(--color-text-muted)]">
                          <Clock className="inline h-2.5 w-2.5" /> {formatInterval(inst.schedule.interval_sec as number)}
                        </span>
                      )}
                      <span className="text-[var(--color-text-muted)]">{inst.run_count} runs</span>
                      <button
                        onClick={() => stopMutation.mutate(inst.instance_id)}
                        disabled={stopMutation.isPending}
                        className="ml-0.5 rounded p-0.5 text-[var(--color-red)] hover:bg-[var(--color-red)]/10"
                        title="Stop"
                      >
                        <Trash2 className="h-2.5 w-2.5" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Routine actions">
            {activeRoutine && runServer && (
              <>
                <button
                  type="button"
                  onClick={() => runMutation.mutate()}
                  disabled={runMutation.isPending || !runServer}
                  className={ROUTINE_PRIMARY_ACTION}
                  title="Run with current config"
                >
                  {runMutation.isPending ? <Spinner /> : <Play />}
                  Run
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setShowNotifyPanel(false);
                    if (showSetup) {
                      const field = setupRef.current?.querySelector<HTMLElement>("input, select, button");
                      field?.scrollIntoView({ block: "nearest" });
                      field?.focus({ preventScroll: true });
                    } else {
                      setShowConfigPanel((v) => !v);
                    }
                  }}
                  aria-expanded={showSetup || showConfigPanel}
                  className={ROUTINE_ACTION}
                  title="Configure & Run"
                >
                  <Settings2 /> Config
                </button>
                {!activeRoutine.is_continuous && (
                  <ScheduleDropdown
                    onSchedule={(sec) => scheduleMutation.mutate(sec)}
                    disabled={scheduleMutation.isPending || !runServer}
                  />
                )}
              </>
            )}
            <div className="contents @max-[639px]/routine-browser:hidden">{secondaryActions}</div>
            <div className="hidden shrink-0 @max-[639px]/routine-browser:block">
              <button
                ref={setMoreAnchor}
                type="button"
                aria-label="More routine actions"
                aria-expanded={moreOpen}
                aria-haspopup="true"
                onClick={() => setMoreOpen(!moreOpen)}
                className={ROUTINE_ACTION}
              >
                <MoreHorizontal />
              </button>
              <AnchoredMenu anchor={moreAnchor} open={moreOpen} onClose={() => setMoreOpen(false)} minWidth={200} align="right">
                <div data-routine-more-panel className="flex flex-col gap-2 p-2 [&_button]:w-full [&_button]:justify-start" onClick={(event) => {
                  if ((event.target as HTMLElement).closest("button")) setMoreOpen(false);
                }}>
                  {secondaryActions}
                </div>
              </AnchoredMenu>
            </div>
          </div>
          {!hosted && !page && onClose && (
            <IconButton
              label="Close (Esc)"
              size="lg"
              onClick={onClose}
            >
              <X className="h-4 w-4" />
            </IconButton>
          )}
        </div>

        </div>

        {/* Panel height is relative to the space below the header, so even a
            mobile toolbar cannot let configuration squeeze the report away. */}
        <div className={`flex min-h-0 flex-col ${page ? "flex-[1_0_auto]" : "flex-1"}`}>
          {/* Schedule failure — surfaced outside the collapsible panels so it is
              always visible after a failed ScheduleDropdown action (CORR-097) */}
          {scheduleMutation.isError && (
            <div className="border-b border-[var(--color-border)] bg-[var(--color-red)]/5 px-4 py-2">
              <p className="text-xs text-[var(--color-red)]">
                Could not schedule {activeSource.replace(/_/g, " ")}: {(scheduleMutation.error as Error).message}
              </p>
            </div>
          )}

          {/* The setup view already owns the configuration editor. */}
          {showConfigPanel && activeRoutine && !showSetup && (
            <div className="max-h-[45%] min-h-0 shrink-0 overflow-y-auto border-b border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-4 md:px-6">
              <div className="max-w-4xl">
                <div className="mb-4 flex items-center justify-between gap-3">
                  <h3 className="text-sm font-semibold text-[var(--color-text)]">Configuration</h3>
                  <IconButton
                    label="Close configuration"
                    size="lg"
                    onClick={() => setShowConfigPanel(false)}
                  >
                    <X className="h-4 w-4" />
                  </IconButton>
                </div>
                {configForm}
              </div>
            </div>
          )}
          {runMutation.isError && !showSetup && (
            <p role="alert" className="shrink-0 border-b border-[var(--color-border)] px-4 py-3 text-xs text-[var(--color-red)]">
              {(runMutation.error as Error).message}
            </p>
          )}

          {/* Notifications panel (collapsible) */}
          {showNotifyPanel && activeRoutine && (
            <div className="max-h-[45%] min-h-0 shrink-0 overflow-y-auto border-b border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-4 md:px-6">
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-[11px] font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
                  Notifications
                </h3>
                <IconButton
                  label="Close notifications"
                  size="sm"
                  onClick={() => setShowNotifyPanel(false)}
                >
                  <X className="h-3 w-3" />
                </IconButton>
              </div>
              <RoutineHooksPanel routineName={activeSource} collapsible={false} />
            </div>
          )}

          {(hasOutput || (view === "report" && selectedReport)) && (
            <div aria-label="Report controls" className="flex shrink-0 flex-wrap items-center gap-2 border-b border-[var(--color-border)] px-4 py-2 md:px-6 @max-[639px]/routine-browser:px-3 pointer-coarse:[&_button]:min-h-9 pointer-coarse:[&_button]:min-w-9">
              {/* What this routine's last run said, when it said it in text
                  rather than in a report — and the way back to the reports. */}
              {hasOutput && (
                <div className="mr-1 flex items-center gap-0.5 rounded bg-[var(--color-surface-hover)] p-0.5">
                  {(["report", "output"] as const).map((id) => (
                    <button
                      key={id}
                      onClick={() => setView(id)}
                      className={`min-h-7 rounded-md px-3 py-1 text-xs font-medium capitalize transition-colors pointer-coarse:min-h-9 ${
                        view === id
                          ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                          : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                      }`}
                      title={
                        id === "report"
                          ? "The pages this routine wrote"
                          : "What its last run handed back"
                      }
                    >
                      {id}
                    </button>
                  ))}
                </div>
              )}
              <div className="flex items-center gap-1">
                {/* Report navigation */}
                {view === "report" && reports.length > 1 && (
                  <>
                    <span className="mr-1 text-xs text-[var(--color-text-muted)]">
                      {selectedReportIdx + 1} of {reports.length}
                    </span>
                    <IconButton
                      label="Previous report"
                      onClick={goPrevReport}
                      disabled={selectedReportIdx === 0}
                    >
                      <ChevronLeft className="h-4 w-4" />
                    </IconButton>
                    <IconButton
                      label="Next report"
                      onClick={goNextReport}
                      disabled={selectedReportIdx >= reports.length - 1}
                    >
                      <ChevronRight className="h-4 w-4" />
                    </IconButton>
                  </>
                )}
                {/* Download */}
                {view === "report" && selectedReport && (
                  <IconButton
                    label="Download report"
                    onClick={downloadReport}
                  >
                    <Download className="h-4 w-4" />
                  </IconButton>
                )}
                {/* Delete this report, or every report the routine has made */}
                {view === "report" && selectedReport && (
                  <>
                    <IconButton
                      label="Delete report"
                      tone="danger"
                      onClick={() => setConfirmDelete({ kind: "one", source: activeSource, id: selectedReport.id, label: selectedReport.title || selectedReport.filename })}
                      disabled={deleting}
                    >
                      <Trash2 className="h-4 w-4" />
                    </IconButton>
                    <IconButton
                      label="Delete all reports"
                      title={`Delete all ${reportsData?.total ?? reports.length} reports from this routine`}
                      tone="danger"
                      onClick={() => setConfirmDelete({ kind: "all", source: activeSource, total: reportsData?.total ?? reports.length })}
                      disabled={deleting}
                    >
                      <ListX className="h-4 w-4" />
                    </IconButton>
                  </>
                )}
              </div>
            </div>
          )}

          {/* Report timeline strip at top */}
          {view === "report" && reports.length > 1 && (
            <div className="flex min-w-0 shrink-0 items-center gap-1 overflow-x-auto border-b border-[var(--color-border)]/50 px-4 py-1.5 md:px-6 @max-[639px]/routine-browser:px-3 [&>*]:shrink-0 [&>*]:whitespace-nowrap">
              {reports.slice(0, 10).map((r, idx) => (
                <button
                  key={r.id}
                  onClick={() => setSelectedReportIdx(idx)}
                  className={`min-h-7 shrink-0 rounded-md px-2 py-1 text-xs transition-colors pointer-coarse:min-h-9 ${
                    idx === selectedReportIdx
                      ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)] font-medium"
                      : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                  }`}
                  title={r.title}
                >
                  {formatAgo(r.created_at)}
                </button>
              ))}
              {reports.length > 10 && (
                <div className="ml-auto w-56 max-w-full shrink-0">
                  <SelectField
                    label={`+${reports.length - 10} older reports`}
                    value={selectedReportIdx >= 10 ? reports[selectedReportIdx]?.id ?? "" : ""}
                    onChange={(id) => {
                      const index = reports.findIndex((report) => report.id === id);
                      if (index >= 10) setSelectedReportIdx(index);
                    }}
                    size="compact"
                    options={reports.slice(10).map((report) => ({
                      value: report.id,
                      label: new Date(report.created_at).toLocaleString(undefined, {
                        month: "short",
                        day: "numeric",
                        hour: "2-digit",
                        minute: "2-digit",
                      }) + (report.title !== reports[0]?.title ? ` — ${report.title}` : ""),
                    }))}
                  />
                </div>
              )}
            </div>
          )}

          {/* Report content */}
          <div className={`relative min-h-0 min-w-0 ${page ? "flex min-h-48 flex-[1_0_auto] flex-col overflow-visible [&>div]:h-auto [&>div]:overflow-visible" : "flex-1 overflow-hidden @max-[639px]/routine-browser:pb-[env(safe-area-inset-bottom)]"}`}>
            {view === "output" ? (
              <div className="h-full overflow-auto px-4 py-3">
                {!shownRun ? (
                  <p className="text-xs text-[var(--color-text-muted)]">
                    This routine has no run in memory.
                  </p>
                ) : shownRun.error ? (
                  <pre className="whitespace-pre-wrap break-words font-mono text-xs text-[var(--color-red)]">
                    {shownRun.error}
                  </pre>
                ) : shownRun.has_result || shownRun.result_text ? (
                  <RoutineResultView instance={shownRun} />
                ) : (
                  <p className="whitespace-pre-wrap text-xs text-[var(--color-text-muted)]">
                    {shownRun.last_result || "(no output yet)"}
                  </p>
                )}
              </div>
            ) : loadingReports ? (
              <div className="flex h-full items-center justify-center">
                <Spinner size="xl" tone="muted" />
              </div>
            ) : !selectedReport ? (
              <div className="flex h-full grow overflow-y-auto px-3 py-4 text-left md:px-6 md:py-6">
                {failedRun ? (
                  <div className="m-auto w-full max-w-4xl rounded-lg border border-[var(--color-red)]/30 bg-[var(--color-red)]/5 p-5">
                    <div className="mb-3 flex items-center gap-2">
                      <AlertTriangle className="h-5 w-5 text-[var(--color-red)]" />
                      <h3 className="text-sm font-semibold text-[var(--color-red)]">Routine Failed</h3>
                    </div>
                    <pre className="max-h-60 overflow-y-auto whitespace-pre-wrap break-words rounded bg-[var(--color-surface)] p-3 font-mono text-xs text-[var(--color-text-muted)]">
                      {failedRun.error || "Unknown error"}
                    </pre>
                    {activeRoutine && runServer && (
                      <button
                        onClick={() => runMutation.mutate()}
                        disabled={runMutation.isPending}
                        className="mt-4 flex min-h-10 items-center gap-2 rounded-md bg-[var(--color-primary)] px-4 py-2 text-sm font-semibold text-[var(--on-primary)] hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
                      >
                        {runMutation.isPending ? <Spinner /> : <Play className="h-4 w-4" />}
                        Retry
                      </button>
                    )}
                  </div>
                ) : (
                  <div className="m-auto w-full max-w-4xl">
                    <div className="mb-3 flex items-center gap-2 text-xs">
                      <Zap className="h-3.5 w-3.5 shrink-0 text-[var(--color-primary)]" />
                      <p className="min-w-0 truncate"
                        title={externalPicker && activeRoutine?.description ? activeRoutine.description : "Review the configuration below, then run this routine to get started."}>
                        <span className="font-medium text-[var(--color-text)]">No reports yet</span>
                        <span className="text-[var(--color-text-muted)]">
                          {" · "}{externalPicker && activeRoutine?.description ? activeRoutine.description : "Review the configuration below, then run this routine to get started."}
                        </span>
                      </p>
                    </div>
                    {activeRoutine && (
                      <div ref={setupRef} className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
                        <div className="border-b border-[var(--color-border)] px-4 py-2.5">
                          <h3 className="text-sm font-semibold text-[var(--color-text)]">Configuration</h3>
                        </div>
                        <div className="p-4">{configForm}</div>
                        {runServer && (
                          <div className="flex flex-wrap items-center gap-3 border-t border-[var(--color-border)] px-4 py-3">
                            <button
                              onClick={() => runMutation.mutate()}
                              disabled={runMutation.isPending}
                              className="flex h-9 items-center gap-2 rounded-md bg-[var(--color-primary)] px-4 text-sm font-semibold text-[var(--on-primary)] transition-colors hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
                            >
                              {runMutation.isPending ? <Spinner /> : <Play className="h-4 w-4" />}
                              Run for the first time
                            </button>
                            {runMutation.isError && (
                              <p role="alert" className="min-w-0 break-words text-xs text-[var(--color-red)]">{(runMutation.error as Error).message}</p>
                            )}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ) : (
              <ReportFrame key={selectedReport.id} reportId={selectedReport.id} title={selectedReport.title} fitContent={page} />
            )}

            {/* Chevron overlays for report navigation */}
            {view === "report" && selectedReport && selectedReportIdx > 0 && (
              <button
                onClick={goPrevReport}
                className="@max-[639px]/routine-browser:hidden absolute left-3 top-1/2 -translate-y-1/2 rounded-full bg-black/40 p-2 text-white/60 hover:bg-black/60 hover:text-white transition-all"
                title="Previous report"
                aria-label="Previous report"
              >
                <ChevronLeft className="h-5 w-5" />
              </button>
            )}
            {view === "report" &&
              selectedReport &&
              selectedReportIdx < reports.length - 1 && (
              <button
                onClick={goNextReport}
                className="@max-[639px]/routine-browser:hidden absolute right-3 top-1/2 -translate-y-1/2 rounded-full bg-black/40 p-2 text-white/60 hover:bg-black/60 hover:text-white transition-all"
                title="Next report"
                aria-label="Next report"
              >
                <ChevronRight className="h-5 w-5" />
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Source code modal */}
      {confirmDelete?.kind === "one" && (
        <ConfirmDialog
          title="Delete this report?"
          confirmLabel="Delete"
          pending={deleteMutation.isPending}
          error={deleteMutation.error instanceof Error ? deleteMutation.error.message : null}
          onConfirm={() => deleteMutation.mutate(confirmDelete)}
          onCancel={closeDeleteDialog}
        >
          <p>{confirmDelete.label}</p>
        </ConfirmDialog>
      )}
      {confirmRunAll && (
        <ConfirmDialog
          title={`Run ${filteredRoutines.length} routines?`}
          confirmLabel="Run all"
          tone="primary"
          onConfirm={() => {
            setConfirmRunAll(false);
            void runAll();
          }}
          onCancel={() => setConfirmRunAll(false)}
        >
          {/* The one thing worth saying: what keeps running afterwards. */}
          {filteredRoutines.some((r) => r.is_continuous) && (
            <p>
              {filteredRoutines.filter((r) => r.is_continuous).length} of them keep running until you stop them.
            </p>
          )}
        </ConfirmDialog>
      )}
      {confirmDelete?.kind === "all" && (
        <ConfirmDialog
          title={`Delete all ${confirmDelete.total} reports?`}
          confirmLabel="Delete all"
          pendingLabel="Deleting…"
          pending={deleteAllMutation.isPending}
          error={deleteAllError}
          onConfirm={() => deleteAllMutation.mutate(confirmDelete.source)}
          onCancel={closeDeleteDialog}
        >
          <p>{formatRoutineName(confirmDelete.source)}</p>
        </ConfirmDialog>
      )}

      {showSourceModal && (
        <div className={FULLSCREEN_LAYER_TOP}>
          <div className="flex items-center justify-between border-b border-[var(--color-border)] px-4 py-2">
            <div className="flex items-center gap-3 min-w-0">
              <Code className="h-4 w-4 text-[var(--color-text-muted)]" />
              <span className="text-sm font-semibold text-[var(--color-text)]">
                {sourceData?.filename ?? activeSource}
              </span>
            </div>
            <IconButton
              label="Close (Esc)"
              onClick={() => setShowSourceModal(false)}
            >
              <X className="h-4 w-4" />
            </IconButton>
          </div>
          <div className="flex-1 overflow-auto bg-[var(--color-surface)]">
            {sourceData ? (
              <pre className="p-0 m-0 text-xs leading-relaxed">
                {sourceData.source.split("\n").map((line, i) => (
                  <div key={i} className="flex hover:bg-[var(--color-surface-hover)]">
                    <span className="sticky left-0 w-12 shrink-0 select-none bg-[var(--color-surface)] pr-3 text-right font-mono text-[var(--color-text-muted)]/40 border-r border-[var(--color-border)]/30">
                      {i + 1}
                    </span>
                    <code className="pl-4 font-mono text-[var(--color-text)] whitespace-pre">{line}</code>
                  </div>
                ))}
              </pre>
            ) : (
              <div className="flex h-full items-center justify-center">
                <Spinner size="xl" tone="muted" />
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
