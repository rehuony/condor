import type { ComponentPropsWithoutRef } from "react";
import ReactMarkdown, { defaultUrlTransform, type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { useAuthedImage } from "@/hooks/useAuthedImage";
import { researchApiHref, researchLink } from "@/lib/research-links";

const GFM = [remarkGfm];

// Reading typography for long reports: roomier than the dashboard, tighter on phones.
const HEADING = "mb-[0.85em] mt-[2.25em] font-semibold leading-snug text-pretty";
const MUTED_TEXT = "text-[color-mix(in_srgb,var(--color-text)_75%,var(--color-text-muted))]";

/** react-markdown hands each renderer its AST node; it is not a DOM attribute. */
function dom<T extends { node?: unknown }>({ node, ...rest }: T): Omit<T, "node"> {
  void node;
  return rest;
}

export function ResearchImage({ path, alt = "" }: { path: string; alt?: string }) {
  const { src, status } = useAuthedImage(researchApiHref(path, true));
  if (status === "loading") return <span role="status">Loading image…</span>;
  if (!src) return <span>Image unavailable{alt ? `: ${alt}` : ""}</span>;
  return <img src={src} alt={alt} className="mx-auto my-6 block h-auto max-w-full rounded-lg" />;
}

function components(path: string): Components {
  return {
    h1: (props) => <h1 className={`${HEADING} text-xl md:text-2xl`} {...dom(props)} />,
    h2: (props) => <h2 className={`${HEADING} text-lg md:text-xl`} {...dom(props)} />,
    h3: (props) => <h3 className={`${HEADING} text-base md:text-[1.0625rem]`} {...dom(props)} />,
    h4: (props) => <h4 className={`${HEADING} text-base`} {...dom(props)} />,
    h5: (props) => <h5 className={`${HEADING} text-base`} {...dom(props)} />,
    h6: (props) => <h6 className={`${HEADING} text-base`} {...dom(props)} />,
    p: (props) => <p className="my-[1.1em]" {...dom(props)} />,
    strong: (props) => <strong className="font-semibold" {...dom(props)} />,
    ul: ({ children }) => <ul className="my-[1.1em] list-disc pl-6 [li_&]:my-2">{children}</ul>,
    ol: ({ children, start }) => <ol className="my-[1.1em] list-decimal pl-6 [li_&]:my-2" start={start}>{children}</ol>,
    li: (props) => <li className="my-[0.4em] pl-1 marker:text-[var(--color-text-muted)]" {...dom(props)} />,
    a({ href = "", children }) {
      const file = researchLink(href, path);
      return (
        <a
          href={file?.href ?? href}
          target={file || href.startsWith("#") ? undefined : "_blank"}
          rel="noopener noreferrer"
          className="text-[var(--color-primary)] underline decoration-[var(--color-primary)]/45 underline-offset-4 hover:decoration-current"
        >
          {children}
        </a>
      );
    },
    img({ src = "", alt }) {
      const file = researchLink(src, path);
      return file
        ? <ResearchImage path={file.path} alt={alt} />
        : <img src={src} alt={alt ?? ""} className="mx-auto my-6 block h-auto max-w-full rounded-lg" loading="lazy" />;
    },
    table: ({ children }) => (
      <div className="my-6 overflow-x-auto rounded-[10px] border border-[var(--color-border)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-primary)]"
        tabIndex={0} role="region" aria-label="Data table">
        <table className="w-full border-collapse text-sm leading-[1.65] tabular-nums">{children}</table>
      </div>
    ),
    tr: (props) => <tr className="[tbody_&]:hover:bg-[var(--color-surface-hover)]/50" {...dom(props)} />,
    th: (props: ComponentPropsWithoutRef<"th"> & { node?: unknown }) => (
      <th className="min-w-[7.5rem] whitespace-nowrap border-b border-[var(--color-border)] bg-[var(--color-surface-hover)] px-4 py-3 text-left text-[13px] font-medium" {...dom(props)} />
    ),
    td: (props: ComponentPropsWithoutRef<"td"> & { node?: unknown }) => (
      <td className="min-w-[7.5rem] border-b border-[var(--color-border)] px-4 py-3 text-left [overflow-wrap:normal] [tr:last-child_&]:border-b-0" {...dom(props)} />
    ),
    blockquote: (props) => (
      <blockquote className={`my-6 rounded-r-lg border-l-[3px] border-[var(--color-primary)]/60 bg-[color-mix(in_srgb,var(--color-primary)_5%,var(--color-surface))] px-5 py-3 ${MUTED_TEXT} [&>:first-child]:mt-0 [&>:last-child]:mb-0`} {...dom(props)} />
    ),
    pre: (props) => (
      <pre className="my-6 overflow-x-auto rounded-[10px] border border-[var(--color-border)] bg-[var(--chat-inset)] p-4 text-[13px] leading-[1.8] [tab-size:2] md:p-5" {...dom(props)} />
    ),
    code: (props) => (
      <code className="rounded bg-[var(--chat-inset)] px-[0.35em] py-[0.15em] text-[0.85em] [pre_&]:bg-transparent [pre_&]:p-0 [pre_&]:text-[length:inherit]" {...dom(props)} />
    ),
    hr: () => <hr className="my-8 border-[var(--color-border)]" />,
  };
}

/** Markdown is rendered as React elements; embedded HTML never executes. */
export function ResearchMarkdown({ content, path }: { content: string; path: string }) {
  return (
    <div className="text-[15px] leading-[1.7] [overflow-wrap:anywhere] md:text-base md:leading-[1.85] [&>:first-child]:mt-0 [&>:last-child]:mb-0">
      <ReactMarkdown
        remarkPlugins={GFM}
        urlTransform={(url) => researchLink(url, path)?.href ?? defaultUrlTransform(url)}
        components={components(path)}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
