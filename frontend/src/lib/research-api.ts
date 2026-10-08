import { authFetch } from "./auth-token";
import { researchApiHref } from "./research-links";

export interface ResearchFile {
  path: string;
  name: string;
  size: number;
  modified_at: string;
  title: string;
  summary: string;
  created_at: string | null;
  preview: "markdown" | "text" | "image" | "none";
  content: string | null;
  body: string | null;
}

async function readFile(path: string, download: boolean, signal?: AbortSignal): Promise<Response> {
  const response = await authFetch(researchApiHref(path, download), { signal });
  if (!response.ok) {
    const message = response.status === 404 ? "This research file is missing or no longer available."
      : response.status === 403 ? "Only administrators can access this install's research files."
      : response.status === 401 ? "Your session has expired. Please sign in again."
      : "Unable to load this research file. Please try again.";
    throw new Error(message);
  }
  return response;
}

export async function getResearchFile(path: string, signal?: AbortSignal): Promise<ResearchFile> {
  return (await readFile(path, false, signal)).json();
}

export async function downloadResearchFile(path: string): Promise<void> {
  const blob = await (await readFile(path, true)).blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = path.split("/").pop() || "research";
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Give the browser time to start the download before releasing its bytes.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
