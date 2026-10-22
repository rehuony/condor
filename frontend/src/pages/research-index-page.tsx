import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, FileText } from "lucide-react";
import { Link } from "react-router-dom";

import { Spinner } from "@/components/ui/Spinner";
import { listResearch, type ResearchStudy } from "@/lib/api/research";
import { researchHref } from "@/lib/research-links";

const ACTION = "inline-flex h-8 items-center justify-center gap-1.5 rounded-md px-2 text-xs font-medium text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-primary)]";

function studyDate(study: ResearchStudy) {
  return new Date(study.created_at ?? study.modified_at).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
}

/** Topics in the order of their newest study; the list arrives newest first. */
function byTopic(studies: ResearchStudy[]) {
  const topics = new Map<string, ResearchStudy[]>();
  for (const study of studies) topics.set(study.topic, [...(topics.get(study.topic) ?? []), study]);
  return [...topics];
}

export function ResearchIndexPage() {
  const { data: studies, isPending, error, refetch } = useQuery({
    queryKey: ["research-index"],
    queryFn: ({ signal }) => listResearch(signal),
    retry: false,
  });

  return (
    <section className="mx-auto w-full max-w-5xl pb-12 text-[var(--color-text)] sm:pt-2">
      <nav aria-label="Research actions" className="mb-3 flex min-w-0 items-center gap-1 md:mb-4">
        <Link to="/" className={`${ACTION} -ml-2 shrink-0`} aria-label="Back to chat">
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          <span className="hidden sm:inline">Back to chat</span><span className="sm:hidden">Chat</span>
        </Link>
      </nav>

      <div className="md:rounded-2xl md:border md:border-[var(--color-border)] md:bg-[var(--color-surface)] md:shadow-sm md:shadow-black/5">
        <div className="mx-auto max-w-[54rem] py-2 md:px-12 md:py-12">
          <header className="mb-6 border-b border-[var(--color-border)] pb-5 md:mb-8 md:pb-7">
            <h1 className="text-xl font-semibold tracking-tight md:text-[2.125rem]">Research</h1>
            {studies && (
              <p className="mt-3 text-xs text-[var(--color-text-muted)] md:text-[13px]">
                {studies.length} {studies.length === 1 ? "study" : "studies"} in {byTopic(studies).length} {byTopic(studies).length === 1 ? "topic" : "topics"}
              </p>
            )}
          </header>

          {isPending && (
            <p role="status" className="flex items-center justify-center gap-2 py-16 text-sm text-[var(--color-text-muted)]">
              <Spinner /> Loading research…
            </p>
          )}
          {error && (
            <div role="alert" className="py-8 text-sm">
              <p>{error.message}</p>
              <button className={`${ACTION} mt-3 border border-[var(--color-border)]`} onClick={() => void refetch()}>Try again</button>
            </div>
          )}
          {studies?.length === 0 && (
            <p className="py-8 text-sm text-[var(--color-text-muted)]">No research yet. Studies appear here once an agent writes one.</p>
          )}

          {studies && byTopic(studies).map(([topic, items]) => (
            <section key={topic} aria-labelledby={`topic-${topic}`} className="mb-8 last:mb-0">
              <h2 id={`topic-${topic}`} className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
                {topic} <span className="font-normal normal-case tracking-normal">· {items.length}</span>
              </h2>
              <ul className="divide-y divide-[var(--color-border)]">
                {items.map((study) => (
                  <li key={study.path}>
                    <Link to={researchHref(study.path)} className="group -mx-2 flex gap-3 rounded-md px-2 py-3 hover:bg-[var(--color-surface-hover)] focus-visible:outline-2 focus-visible:outline-[var(--color-primary)]">
                      <FileText className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-text-muted)]" aria-hidden="true" />
                      <span className="min-w-0 flex-1">
                        <span className="flex items-baseline justify-between gap-3">
                          <span className="min-w-0 truncate text-sm font-medium">{study.title}</span>
                          <time dateTime={study.created_at ?? study.modified_at} className="shrink-0 text-xs tabular-nums text-[var(--color-text-muted)]">{studyDate(study)}</time>
                        </span>
                        {study.summary && <span className="mt-1 line-clamp-2 text-xs leading-5 text-[var(--color-text-muted)]">{study.summary}</span>}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      </div>
    </section>
  );
}
