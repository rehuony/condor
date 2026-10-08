/** Public addresses contain only the path below .condor/research/. */
export function researchHref(path: string): string {
  return `/research/${path.split("/").map(encodeURIComponent).join("/")}`;
}

export function researchApiHref(path: string, download = false): string {
  return `/api/v1${researchHref(path)}${download ? "?download=1" : ""}`;
}

/** Recognize current links and the filesystem links in persisted conversations.
 * External websites are never reinterpreted as local files. Relative links in a
 * report resolve against that report, not against the dashboard or repository. */
export function researchLink(href: string, basePath?: string): { path: string; href: string } | null {
  if (!href || href.startsWith("#")) return null;
  const origin = typeof window === "undefined" ? "http://localhost" : window.location.origin;
  let value = href;
  if (/^(?:https?:|file:|\/\/)/i.test(value)) {
    try {
      const url = new URL(value, origin);
      if (url.protocol === "file:") {
        if (url.hostname && url.hostname !== "localhost") return null;
      } else if (url.origin !== origin) return null;
      value = url.pathname + url.hash;
    } catch { return null; }
  } else if (basePath && !value.startsWith("/") && !value.startsWith(".condor/") && !/^[a-z][a-z\d+.-]*:/i.test(value)) {
    value = new URL(value, origin + researchHref(basePath)).pathname + (value.includes("#") ? value.slice(value.indexOf("#")) : "");
  }

  const [rawPath, hash] = value.split("#", 2);
  let decoded: string;
  try { decoded = decodeURIComponent(rawPath.split("?", 1)[0].replace(/%(?![\da-f]{2})/gi, "%25")); }
  catch { return null; }
  const legacy = decoded.match(/(?:^|\/)\.condor\/research\/(.+)$/);
  const path = legacy ? legacy[1].replace(/:\d+(?::\d+)?$/, "") : decoded.match(/^\/research\/(.+)$/)?.[1];
  if (!path || [...path].some((char) => char === "\\" || char.charCodeAt(0) < 32) || path.split("/").some((part) => !part || part.startsWith("."))) return null;
  return { path, href: researchHref(path) + (hash ? `#${hash}` : "") };
}
