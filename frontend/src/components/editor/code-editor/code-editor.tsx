import { lazy, Suspense } from "react";

import { Spinner } from "@/components/ui/Spinner";

import type { CodeEditorProps } from "./code-editor.types";

const LazyCodeEditor = lazy(() =>
  import("./code-editor-implementation").then(({ CodeEditorImplementation }) => ({
    default: CodeEditorImplementation,
  })),
);

export function CodeEditor(props: CodeEditorProps) {
  return (
    <Suspense
      fallback={
        <div
          className={`flex items-center justify-center rounded-md border border-[var(--color-border)] ${props.className ?? ""}`}
          style={{ height: props.height ?? "400px" }}
        >
          <Spinner size="lg" tone="muted" label="Loading editor" />
        </div>
      }
    >
      <LazyCodeEditor {...props} />
    </Suspense>
  );
}
