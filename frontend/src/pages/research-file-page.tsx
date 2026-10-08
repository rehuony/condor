import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, BookOpen, CalendarDays, Check, ChevronDown, Code2, Copy, Download, FileText, Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";

import { ResearchImage, ResearchMarkdown } from "@/components/research-markdown";
import { copyText } from "@/lib/clipboard";
import { downloadResearchFile, getResearchFile } from "@/lib/research-api";
import { researchHref, researchLink } from "@/lib/research-links";

const ACTION = "inline-flex min-h-10 items-center justify-center gap-2 rounded-lg px-3 text-sm font-medium focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-primary)] disabled:cursor-not-allowed disabled:opacity-40";
const SECONDARY_ACTION = `${ACTION} text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]`;

function fileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  return bytes < 1024 * 1024
    ? `${(bytes / 1024).toFixed(1)} KB`
    : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function ResearchFilePage() {
  const { pathname } = useLocation();
  const file = researchLink(pathname);
  return file ? <ResearchFileView key={file.path} path={file.path} /> : (
    <p role="alert" className="text-sm text-[var(--color-red)]">This research link is invalid.</p>
  );
}

function ResearchFileView({ path }: { path: string }) {
  const [source, setSource] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState("");
  const [copied, setCopied] = useState(false);
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (copyTimer.current) clearTimeout(copyTimer.current);
  }, []);

  const { data: file, isPending, error, refetch } = useQuery({
    queryKey: ["research-file", path],
    queryFn: ({ signal }) => getResearchFile(path, signal),
    retry: false,
  });

  async function download() {
    setDownloading(true);
    setDownloadError("");
    try {
      await downloadResearchFile(path);
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : "Download failed. Please try again.");
    } finally {
      setDownloading(false);
    }
  }

  async function copyLink() {
    if (!await copyText(window.location.origin + researchHref(path))) return;
    setCopied(true);
    if (copyTimer.current) clearTimeout(copyTimer.current);
    copyTimer.current = setTimeout(() => setCopied(false), 1500);
  }

  return (
    <section className="mx-auto w-full max-w-5xl pb-12 text-[var(--color-text)] sm:pt-2">
      <nav aria-label="Research actions" className="mb-4 flex flex-wrap items-center justify-between gap-2 sm:mb-6">
        <Link to="/" className={`${SECONDARY_ACTION} -ml-3`} aria-label="Back to chat">
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          <span className="hidden sm:inline">Back to chat</span><span className="sm:hidden">Chat</span>
        </Link>
        <div className="flex items-center gap-2">
          <button className={SECONDARY_ACTION} aria-label={copied ? "Copied" : "Copy link"} title={copied ? "Copied" : "Copy link"} onClick={() => void copyLink()}>
            {copied ? <Check className="h-4 w-4" aria-hidden="true" /> : <Copy className="h-4 w-4" aria-hidden="true" />}
            <span className="hidden sm:inline">{copied ? "Copied" : "Copy link"}</span>
          </button>
          <button className={`${ACTION} bg-[var(--color-primary)] text-[var(--on-primary)] hover:bg-[var(--color-primary-hover)]`} disabled={!file || downloading} onClick={() => void download()}>
            {downloading ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Download className="h-4 w-4" aria-hidden="true" />}
            Download
          </button>
        </div>
      </nav>

      {downloadError && <p role="alert" className="mb-4 rounded-lg border border-[var(--color-red)]/25 bg-[var(--color-red)]/5 px-4 py-3 text-sm text-[var(--color-red)]">{downloadError}</p>}

      <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] shadow-sm shadow-black/5 sm:rounded-2xl">
        <div className="flex flex-col items-start justify-between gap-3 border-b border-[var(--color-border)] px-5 py-3 sm:flex-row sm:items-center sm:gap-4 sm:px-8">
          <div className="flex w-full min-w-0 flex-1 items-center gap-2.5 sm:w-auto">
            <FileText className="h-4 w-4 shrink-0 text-[var(--color-text-muted)]" aria-hidden="true" />
            <span className="truncate text-sm font-medium" title={file?.name ?? path.split("/").pop()}>{file?.name ?? path.split("/").pop()}</span>
            {file && <span className="ml-auto shrink-0 text-xs tabular-nums text-[var(--color-text-muted)] sm:ml-0">{fileSize(file.size)}</span>}
          </div>
          {file?.preview === "markdown" && (
            <div className="research-view-toggle" role="group" aria-label="File view">
              <button aria-pressed={!source} onClick={() => setSource(false)}><BookOpen className="h-3.5 w-3.5" aria-hidden="true" />Preview</button>
              <button aria-pressed={source} onClick={() => setSource(true)}><Code2 className="h-3.5 w-3.5" aria-hidden="true" />Source</button>
            </div>
          )}
        </div>

        <div className="mx-auto max-w-[54rem] px-5 py-7 sm:px-12 sm:py-12">
          <header className="mb-8 border-b border-[var(--color-border)] pb-7 sm:mb-10 sm:pb-9">
            <h1 className="break-words text-2xl font-semibold leading-snug tracking-tight text-balance sm:text-[2.125rem]">
              {file?.title ?? path.split("/").pop()}
            </h1>
            {file && (
              <p className="mt-4 flex items-center gap-2 text-xs text-[var(--color-text-muted)] sm:text-[13px]">
                <CalendarDays className="h-3.5 w-3.5" aria-hidden="true" />
                <time dateTime={file.created_at ?? file.modified_at}>
                  {new Date(file.created_at ?? file.modified_at).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" })}
                </time>
              </p>
            )}
            {file?.summary && <p className="research-summary mt-6">{file.summary}</p>}
          </header>

          {isPending && (
            <p role="status" className="flex items-center justify-center gap-2 py-16 text-sm text-[var(--color-text-muted)]">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading file…
            </p>
          )}
          {error && (
            <div role="alert" className="py-8 text-sm">
              <p>{error.message}</p>
              <button className={`${SECONDARY_ACTION} mt-3 border border-[var(--color-border)]`} onClick={() => void refetch()}>Try again</button>
            </div>
          )}
          {file && (
            <article className="min-w-0">
              {file.preview === "markdown" && !source ? (
                <div className="research-markdown"><ResearchMarkdown content={file.body ?? ""} path={path} /></div>
              ) : file.content !== null ? (
                <pre className="research-source">{file.content}</pre>
              ) : file.preview === "image" ? (
                <ResearchImage path={path} alt={file.name} />
              ) : (
                <p className="py-8 text-sm leading-7 text-[var(--color-text-muted)]">Preview is unavailable for this file type or size. Download the original to view it.</p>
              )}
            </article>
          )}
        </div>

        {file && (
          <details className="group border-t border-[var(--color-border)] px-5 text-xs text-[var(--color-text-muted)] sm:px-8">
            <summary className="flex min-h-12 cursor-pointer list-none items-center justify-between gap-4 hover:text-[var(--color-text)] focus-visible:outline-2 focus-visible:outline-[var(--color-primary)] [&::-webkit-details-marker]:hidden">
              File details <ChevronDown className="h-3.5 w-3.5 group-open:rotate-180" aria-hidden="true" />
            </summary>
            <dl className="grid gap-3 pb-5 leading-5 sm:grid-cols-[5rem_1fr] sm:gap-x-6 sm:gap-y-2">
              <dt>Location</dt><dd className="break-all text-[var(--color-text)]">{path}</dd>
              <dt>Updated</dt><dd className="text-[var(--color-text)]">{new Date(file.modified_at).toLocaleString()}</dd>
              <dt>Size</dt><dd className="text-[var(--color-text)]">{fileSize(file.size)}</dd>
            </dl>
          </details>
        )}
      </div>
    </section>
  );
}
