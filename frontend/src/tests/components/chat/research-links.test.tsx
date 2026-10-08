/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ChatMessageView } from "@/components/chat/ChatMessage";
import { researchLink } from "@/lib/research-links";

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => { act(() => root.unmount()); container.remove(); });

describe("research links in persisted and streamed messages", () => {
  const absolute = "/Users/alice/Projects/condor/.condor/research/topic/REVIEW.md";
  for (const live of [true, false]) {
    for (const href of [absolute, `file://${absolute}`, `${window.location.origin}${absolute}`, ".condor/research/topic/REVIEW.md"]) {
      it(`opens a private preview without a local path: ${href}, live=${live}`, () => {
        act(() => root.render(<ChatMessageView live={live} message={{ id: "m", role: "assistant", toolCalls: [], text: `[${absolute}](${href})` }} />));
        const link = container.querySelector("a")!;
        expect(link.getAttribute("href")).toBe("/research/topic/REVIEW.md");
        expect(link.textContent).toBe("REVIEW.md");
        expect(link.target).toBe("_blank");
        expect(container.innerHTML).not.toContain("/Users/");
      });
    }
  }
  it("keeps descriptive labels and ordinary web references", () => {
    act(() => root.render(<ChatMessageView message={{ id: "m", role: "assistant", toolCalls: [], text: `[调研结论](${absolute}) [Source](https://example.com/.condor/research/a.md)` }} />));
    expect(container.querySelector("a")?.textContent).toBe("调研结论");
    expect(container.querySelectorAll("a")[1].href).toBe("https://example.com/.condor/research/a.md");
  });
});

it("encodes Unicode and percent names once and resolves sibling attachments", () => {
  const href = "/research/%E4%B8%BB%E9%A2%98/%E5%A4%8D%E7%9B%98%20100%25.md";
  expect(researchLink(".condor/research/主题/复盘 100%.md")?.href).toBe(href);
  expect(researchLink(href)).toEqual({ path: "主题/复盘 100%.md", href });
  expect(researchLink("../data.json", "topic/day/REVIEW.md")?.href).toBe("/research/topic/data.json");
  expect(researchLink("./notes.md#summary", "topic/day/REVIEW.md")?.href).toBe("/research/topic/day/notes.md#summary");
});

it("does not turn traversal, hidden files or external hosts into research requests", () => {
  for (const path of ["/research/../secret.md", "/research/%2e%2e/secret.md", "/research/.env", "/research/a%5cb.md", "https://example.com/research/a.md", "file://other/research/a.md"]) {
    expect(researchLink(path)).toBeNull();
  }
});
