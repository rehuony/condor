import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";

import { useAuthedImage } from "@/hooks/useAuthedImage";
import { researchApiHref, researchLink } from "@/lib/research-links";

const GFM = [remarkGfm];

export function ResearchImage({ path, alt = "" }: { path: string; alt?: string }) {
  const { src, status } = useAuthedImage(researchApiHref(path, true));
  if (status === "loading") return <span role="status">Loading image…</span>;
  if (!src) return <span>Image unavailable{alt ? `: ${alt}` : ""}</span>;
  return <img src={src} alt={alt} className="mx-auto my-4 h-auto max-w-full rounded-lg" />;
}

/** Markdown is rendered as React elements; embedded HTML never executes. */
export function ResearchMarkdown({ content, path }: { content: string; path: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={GFM}
      urlTransform={(url) => researchLink(url, path)?.href ?? defaultUrlTransform(url)}
      components={{
        a({ href = "", children }) {
          const file = researchLink(href, path);
          return <a href={file?.href ?? href} target={file || href.startsWith("#") ? undefined : "_blank"} rel="noopener noreferrer">{children}</a>;
        },
        img({ src = "", alt }) {
          const file = researchLink(src, path);
          return file ? <ResearchImage path={file.path} alt={alt} /> : <img src={src} alt={alt ?? ""} className="max-w-full" loading="lazy" />;
        },
        table({ children }) {
          return <div className="research-table" tabIndex={0} role="region" aria-label="Data table"><table>{children}</table></div>;
        },
        ul({ children }) {
          return <ul className="list-disc">{children}</ul>;
        },
        ol({ children, start }) {
          return <ol className="list-decimal" start={start}>{children}</ol>;
        },
      }}
    >
      {content}
    </ReactMarkdown>
  );
}
