# Research artifact standard

This standard applies whenever Condor or a domain agent saves research findings,
market snapshots, analysis outputs, or checkpoints. It does not change the normal
locations of source code, tests, maintained documentation, or ReportBuilder's
indexed HTML reports.

## Directory and naming

Start each independent study in a new directory:

```text
.condor/research/<topic>/<YYYY-MM-DD>/<HHmmss>-<short-id>/
├── report.md          # the one user-facing entry point
├── data/              # named JSON/CSV datasets and source snapshots
├── assets/            # referenced PNG/WebP/JPEG figures
└── work/              # scripts, logs, checkpoints and validation results
```

- Use UTC for the directory date/time and a short random id (for example eight
  hex characters) to keep concurrent studies separate. Example:
  `spot-market-making/2026-10-10/093000-a1b2c3d4/report.md`.
- Reuse an existing topic when it fits. Topic names, subdirectories, and supporting
  filenames use lowercase kebab-case. Dates belong in the directory, not repeated
  in each filename. Human titles and report prose use the user's language.
- Create only directories that contain something. A small study can be just
  `report.md`; there is no mandatory manifest, database, or empty scaffolding.
- Keep one report per study. Update that report for corrections to the same study;
  use a new study directory for a new as-of snapshot. Avoid `final-v2`, `latest`,
  `review-final-final`, or multiple competing entry points.
- Keep generated artifacts out of Git. Never put secrets or authentication tokens
  into reports, datasets, screenshots, or source URLs.

## Report format

Start from [research-report.md](research-report.md). Use UTF-8 Markdown with:

```yaml
---
title: "A human-readable research title"
summary: "One or two sentences stating the conclusion and its key qualification."
created_at: "2026-10-10T09:30:00Z"
---
```

The preview uses the title and summary as its opening; metadata is hidden in
reading mode and retained in Source and Download. Follow the frontmatter with
one matching `#` title so the original file remains useful outside Condor.

Use the following body order, translating section names into the user's language:

1. **Key findings** — the most useful evidence and decisions, usually 3–5 points.
2. **Evidence and method** — data as-of time, coverage, units, assumptions, and how
   the result was derived. Distinguish measured facts from estimates.
3. **Risks and limitations** — material gaps and conditions that change the result.
4. **Next steps** — concrete actions or conditions to monitor, not generic advice.
5. **Sources and attachments** — dated primary-source links and descriptive relative
   links to the supporting files actually used.

Omit sections that add no information. Keep the summary short, paragraphs readable,
and tables focused (prefer no more than six columns). Put large tables, raw API
responses, logs, and lengthy code in supporting files; link them from the report.
Use normal Markdown and raster figures, not embedded HTML, scripts, or a custom
page design. Give figures useful alt text. Use `data/prices.json` and
`assets/spread.png` style relative references so the study stays portable.

Data files must identify their source, observation time, timezone, units and
important filters, either in the JSON object or in the report beside a CSV link.
Do not present stale or unavailable figures as a current snapshot.

## Delivery and verification

- In the dashboard, share `[Report title](/research/<relative-path>/report.md)`.
  In a channel requiring an absolute web URL, prefix the configured dashboard
  origin. Links require the recipient to have access; they are not public shares.
- Do not put `/Users/...`, `/home/...`, `file://...`, or other local absolute paths
  in link labels or targets. Show the report's title, not its storage directory.
- Before delivery, check that the report and referenced attachments exist, the
  frontmatter is valid, the summary matches the findings, and the preview and
  original download are readable. Markdown/text preview is capped at 1 MiB;
  larger raw datasets should remain separate downloads.
- When delegating research, pass the allocated study directory and this standard.
  Each worker writes its own named supporting files; the coordinating agent owns
  the final `report.md`. Do not delegate to yourself just to write a file.
- Existing research remains readable without frontmatter. Apply this format to
  new studies; do not bulk rename or move old studies unless requested. If a study
  is moved, preserve its contents and every relative reference.
