# Research artifacts

Store generated analysis, data snapshots, and execution evidence outside Git.
Source code, tests, maintained documentation, and indexed ReportBuilder reports
keep their existing locations.

## Storage

Use one directory and one report per study:

```text
.condor/research/<topic>/<YYYY-MM-DD>/<HHmmss>-<short-id>/
├── report.md    # Reader entry point
├── data/        # Datasets and source snapshots
├── assets/      # Referenced images
└── work/        # Scripts, logs, and checkpoints
```

Use UTC timestamps, a unique short ID, and lowercase kebab-case names. Reuse a
suitable topic and create only the directories needed. Update the same report
for corrections; create a new study for a new observation period. Keep secrets
and credentials out of all artifacts.

When delegating, provide the allocated directory and this standard. Workers own
distinct supporting files; the coordinator owns `report.md`. Preserve contents
and relative references when moving existing studies.

## Report

Start from [the report template](research-report.md). Include YAML `title`,
`summary`, and timezone-aware `created_at`, followed by a matching H1. Use the
requested language for generated reports; maintained templates are in English.

Present findings, evidence and method, limitations, next steps, and sources as
needed. Identify the observation time, coverage, units, filters, and assumptions.
Distinguish observations from estimates and unavailable data from zero.

Keep the report concise. Put large datasets, raw responses, and execution logs
in supporting files and link them with descriptive relative paths. Use Markdown
and raster images; HTML and scripts are not rendered in the reading view.

## Delivery

- Share `[Report title](/research/<relative-path>/report.md)`. Prefix the
  configured dashboard origin when the channel needs an absolute URL.
- Research access currently requires an authenticated administrator. Links do
  not grant access or create public shares.
- Use relative links within a study. Do not expose local absolute paths in
  reader-facing link labels or targets.
- Verify metadata, referenced files, preview, and original download before
  delivery. Text previews are limited to 1 MiB; larger files remain downloads.
- Apply this format to new studies. Existing reports without frontmatter remain
  supported and do not require bulk migration.
