import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, BookOpen, CalendarDays, Check, ChevronDown, Code2, Copy, Download, FileText } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";

import { ResearchImage, ResearchMarkdown } from "@/components/research-markdown";
import { Spinner } from "@/components/ui/Spinner";
import { copyText } from "@/lib/clipboard";
import { downloadResearchFile, getResearchFile } from "@/lib/research-api";
import { researchHref, researchLink } from "@/lib/research-links";

const ACTION = "inline-flex h-8 items-center justify-center gap-1.5 rounded-md px-2 text-xs font-medium text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-primary)] disabled:cursor-not-allowed disabled:opacity-40";
const ICON_ACTION = `${ACTION} w-8 px-0`;
const VIEW_TOGGLE = "inline-flex h-7 items-center gap-1.5 rounded-md border border-transparent px-2 text-xs font-medium text-[var(--color-text-muted)] hover:text-[var(--color-text)] focus-visible:outline-2 focus-visible:outline-[var(--color-primary)] aria-pressed:border-[var(--color-border)] aria-pressed:bg-[var(--color-surface)] aria-pressed:text-[var(--color-text)]";
const SOURCE = "overflow-x-auto rounded-[10px] border border-[var(--color-border)] bg-[var(--chat-inset)] p-4 text-[13px] leading-[1.8] [tab-size:2] md:p-5";

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
      {/* One toolbar: where you came from, what this is, and what you can do with it. */}
      <nav aria-label="Research actions" className="mb-3 flex min-w-0 items-center gap-1 md:mb-4">
        <Link to="/" className={`${ACTION} -ml-2 shrink-0`} aria-label="Back to chat">
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          <span className="hidden sm:inline">Back to chat</span><span className="sm:hidden">Chat</span>
        </Link>
        <span className="mx-1 h-4 w-px shrink-0 bg-[var(--color-border)]" aria-hidden="true" />
        <FileText className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" aria-hidden="true" />
        <span className="min-w-0 truncate text-xs font-medium" title={file?.name ?? path.split("/").pop()}>{file?.name ?? path.split("/").pop()}</span>
        {file && <span className="hidden shrink-0 text-xs tabular-nums text-[var(--color-text-muted)] sm:inline">{fileSize(file.size)}</span>}
        <span className="ml-auto" />
        {file?.preview === "markdown" && (
          <div className="flex shrink-0 gap-0.5 rounded-lg bg-[var(--color-surface-hover)] p-0.5" role="group" aria-label="File view">
            <button className={VIEW_TOGGLE} aria-pressed={!source} onClick={() => setSource(false)} title="Preview">
              <BookOpen className="h-3.5 w-3.5" aria-hidden="true" /><span className="max-sm:sr-only">Preview</span>
            </button>
            <button className={VIEW_TOGGLE} aria-pressed={source} onClick={() => setSource(true)} title="Source">
              <Code2 className="h-3.5 w-3.5" aria-hidden="true" /><span className="max-sm:sr-only">Source</span>
            </button>
          </div>
        )}
        <button className={`${ICON_ACTION} shrink-0`} aria-label={copied ? "Copied" : "Copy link"} title={copied ? "Copied" : "Copy link"} onClick={() => void copyLink()}>
          {copied ? <Check className="h-4 w-4" aria-hidden="true" /> : <Copy className="h-4 w-4" aria-hidden="true" />}
        </button>
        <button className={`${ICON_ACTION} shrink-0`} title="Download original" disabled={!file || downloading} onClick={() => void download()}>
          {downloading ? <Spinner /> : <Download className="h-4 w-4" aria-hidden="true" />}
          <span className="sr-only">Download</span>
        </button>
      </nav>

      {downloadError && <p role="alert" className="mb-4 rounded-lg border border-[var(--color-red)]/25 bg-[var(--color-red)]/5 px-4 py-3 text-sm text-[var(--color-red)]">{downloadError}</p>}

      {/* Phones read the article edge to edge; wider screens keep the page card. */}
      <div className="md:rounded-2xl md:border md:border-[var(--color-border)] md:bg-[var(--color-surface)] md:shadow-sm md:shadow-black/5">
        <div className="mx-auto max-w-[54rem] py-2 md:px-12 md:py-12">
          <header className="mb-6 border-b border-[var(--color-border)] pb-5 md:mb-10 md:pb-9">
            <h1 className="break-words text-xl font-semibold leading-snug tracking-tight text-balance md:text-[2.125rem]">
              {file?.title ?? path.split("/").pop()}
            </h1>
            {file && (
              <p className="mt-3 flex items-center gap-2 text-xs text-[var(--color-text-muted)] md:mt-4 md:text-[13px]">
                <CalendarDays className="h-3.5 w-3.5" aria-hidden="true" />
                <time dateTime={file.created_at ?? file.modified_at}>
                  {new Date(file.created_at ?? file.modified_at).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" })}
                </time>
              </p>
            )}
            {file?.summary && (
              <p className="mt-4 text-[15px] leading-[1.7] text-pretty text-[color-mix(in_srgb,var(--color-text)_75%,var(--color-text-muted))] md:mt-6 md:text-[1.0625rem] md:leading-[1.85]">
                {file.summary}
              </p>
            )}
          </header>

          {isPending && (
            <p role="status" className="flex items-center justify-center gap-2 py-16 text-sm text-[var(--color-text-muted)]">
              <Spinner /> Loading file…
            </p>
          )}
          {error && (
            <div role="alert" className="py-8 text-sm">
              <p>{error.message}</p>
              <button className={`${ACTION} mt-3 border border-[var(--color-border)]`} onClick={() => void refetch()}>Try again</button>
            </div>
          )}
          {file && (
            <article className="min-w-0">
              {file.preview === "markdown" && !source ? (
                <ResearchMarkdown content={file.body ?? ""} path={path} />
              ) : file.content !== null ? (
                <pre className={SOURCE}>{file.content}</pre>
              ) : file.preview === "image" ? (
                <ResearchImage path={path} alt={file.name} />
              ) : (
                <p className="py-8 text-sm leading-7 text-[var(--color-text-muted)]">Preview is unavailable for this file type or size. Download the original to view it.</p>
              )}
            </article>
          )}
        </div>

        {file && (
          <details className="group border-t border-[var(--color-border)] text-xs text-[var(--color-text-muted)] md:px-8">
            <summary className="flex min-h-12 cursor-pointer list-none items-center justify-between gap-4 hover:text-[var(--color-text)] focus-visible:outline-2 focus-visible:outline-[var(--color-primary)] [&::-webkit-details-marker]:hidden">
              File details <ChevronDown className="h-3.5 w-3.5 group-open:rotate-180" aria-hidden="true" />
            </summary>
            <dl className="grid gap-3 pb-5 leading-5 sm:grid-cols-[5rem_1fr] sm:gap-x-6 sm:gap-y-2">
              <dt>Location</dt><dd className="break-all text-[var(--color-text)]">{path}</dd>
              <dt>Updated</dt><dd className="text-[var(--color-text)]">{new Date(file.modified_at).toLocaleString("en-US")}</dd>
              <dt>Size</dt><dd className="text-[var(--color-text)]">{fileSize(file.size)}</dd>
            </dl>
          </details>
        )}
      </div>
    </section>
  );
}
