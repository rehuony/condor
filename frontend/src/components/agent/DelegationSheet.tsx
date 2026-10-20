import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useState } from "react";

import { DelegationDetail } from "@/components/agent/DelegationDetail";
import { DelegationTaskPrompt } from "@/components/agent/DelegationTaskPrompt";
import { DelegationTranscript } from "@/components/agent/DelegationTranscript";
import {
  DELEGATION_STATUS,
  formatDelegationTime,
} from "@/components/agent/delegationStatus";
import { WorkspaceSheet } from "@/components/chat/WorkspaceSheet";
import { api, type Delegation, type DelegationSummary } from "@/lib/api";

/** A consult has no transcript to fetch, and the sheet says why rather than
 *  showing an empty one — the record is a ledger entry, not a tape (FEAT-058). */
const NO_TRANSCRIPT =
  "A consult returns its answer straight to the caller; only background tasks record a transcript.";

/**
 * What the sheet reads off a record it was handed: its listing fields, no body.
 * A full `Delegation` or `DelegationSummary` satisfies it, and so does a run-rail
 * row narrowed to these fields — which need not invent a user, a chat or an end
 * it does not carry (ARCH-398).
 */
export type DelegationListing = Pick<
  DelegationSummary,
  "task_id" | "agent" | "task" | "status" | "kind" | "started_at"
>;

/**
 * One delegation, opened: the ask, then its transcript or its result.
 *
 * Extracted from the context dock once history gained a third caller — the
 * dock, the fleet card and an agent's history all open the same thing, and the
 * one place a delegation is read should not exist in three copies.
 *
 * A caller that already holds the full record (the dock and the fleet card poll
 * `/delegations`, bodies included) passes it and nothing is fetched. A history
 * row carries no body on purpose, so the sheet fetches the record itself —
 * which is also what makes a task recorded by a long-dead process readable here.
 */
export function DelegationSheet({
  task,
  onClose,
}: {
  task: Delegation | DelegationListing;
  onClose: () => void;
}) {
  const isConsult = (task.kind ?? "delegate") === "consult";
  // Follow the task: while it runs there is no result yet, so the transcript
  // *is* the view. Once it is finished the answer is what the task was for, and
  // the transcript is the follow-up question. A consult has no transcript at
  // all, so it opens on the only thing it recorded.
  const [view, setView] = useState<"transcript" | "result">(
    task.status === "running" && !isConsult ? "transcript" : "result",
  );

  const hasBody = "result" in task;
  const { data, error, refetch } = useQuery({
    queryKey: ["delegation", task.task_id],
    queryFn: () => api.getDelegation(task.task_id),
    enabled: !hasBody,
    refetchInterval: (q) => (q.state.data?.status === "running" ? 2000 : false),
  });
  const record = hasBody ? (task as Delegation) : data;

  return (
    <WorkspaceSheet
      title={`Tasks · ${task.agent}`}
      subtitle={`${DELEGATION_STATUS[task.status].label.toLowerCase()} · ${formatDelegationTime(task)}`}
      onClose={onClose}
      bleed
      rememberZen={false}
    >
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        {/* The ask belongs to both views, so it sits above the switch rather
            than inside the one that happens to render it. */}
        <DelegationTaskPrompt text={task.task} />
        <div className="flex shrink-0 items-center gap-1 border-b border-[var(--color-border)] px-4 py-2" aria-label="Task views">
          {(["transcript", "result"] as const).map((id) => (
            <button
              key={id}
              type="button"
              aria-pressed={view === id}
              onClick={() => setView(id)}
              className={`rounded-md px-3 py-1.5 text-xs font-medium capitalize transition-all ${
                view === id
                  ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                  : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              }`}
            >
              {id}
            </button>
          ))}
        </div>
        <div className="min-h-0 min-w-0 flex-1 overflow-y-auto overflow-x-hidden [overflow-wrap:anywhere] p-4">
          {view === "transcript" ? (
            isConsult ? (
              <p className="text-xs text-[var(--color-text-muted)]">{NO_TRANSCRIPT}</p>
            ) : (
              <DelegationTranscript taskId={task.task_id} />
            )
          ) : record ? (
            <DelegationDetail delegation={record} />
          ) : error ? (
            <div role="alert" className="text-sm text-[var(--color-text-muted)]">
              Could not load this task.
              <button type="button" onClick={() => refetch()} className="ml-2 text-[var(--color-primary)]">Retry</button>
            </div>
          ) : (
            <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
              <Loader2 className="h-3 w-3 animate-spin" />
              Loading result…
            </div>
          )}
        </div>
      </div>
    </WorkspaceSheet>
  );
}
