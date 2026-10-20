/// <reference types="node" />

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { compile, optimize } from "@tailwindcss/node";
import { transform } from "lightningcss";
import { expect, it, vi } from "vitest";

function fixedPaneMediaQueries(css: string) {
  const queries: unknown[] = [];
  transform({
    filename: "index.css",
    code: Buffer.from(css),
    visitor: {
      Rule: {
        media(rule) {
          const fixesPane = rule.value.rules.some((child) =>
            child.type === "style"
            && child.value.selectors.some((selector) => selector.some((part) =>
              part.type === "class" && part.name === "narrow-pane:fixed",
            ))
            && child.value.declarations?.declarations?.some((declaration) =>
              declaration.property === "position" && declaration.value.type === "fixed",
            ),
          );
          if (fixesPane) queries.push(...rule.value.query.mediaQueries);
        },
      },
    },
  });
  return queries;
}

it("preserves phone and short-landscape pane styles through CSS optimization", async () => {
  const warnings = vi.spyOn(console, "warn").mockImplementation(() => {});
  try {
    const compiler = await compile(
      readFileSync(new URL("../index.css", import.meta.url), "utf8"),
      { base: fileURLToPath(new URL("..", import.meta.url)), onDependency() {} },
    );
    const css = optimize(compiler.build(["narrow-pane:fixed"]), { minify: true }).code;

    expect(warnings).not.toHaveBeenCalled();
    const queries = fixedPaneMediaQueries(css);
    expect(queries).toHaveLength(2);
    expect(queries).toEqual(fixedPaneMediaQueries(`
      @media (max-width: 767px),
        (min-width: 768px) and (max-width: 1023px) and (max-height: 500px) {
        .narrow-pane\\:fixed { position: fixed; }
      }
    `));
  } finally {
    warnings.mockRestore();
  }
});
