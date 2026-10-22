# Research artifacts

Store generated analysis, data snapshots, and execution evidence outside Git.
Source code, tests, maintained documentation, and indexed ReportBuilder reports
keep their existing locations.

## Storage

Use one directory and one report per study:

```text
data/research/<topic>/<YYYY-MM-DD>/<HHmmss>-<short-id>/
├── report.md    # Reader entry point
├── data/        # Datasets and source snapshots
├── assets/      # Referenced images
└── work/        # Scripts, logs, and checkpoints
```

- **Topic:** the subject, in lowercase kebab-case (`trade-cards`). List
  `data/research/` first and reuse the topic that already covers the subject;
  the angle of a study (a review, a fix, a release) belongs in its title, not in
  a new topic such as `trade-cards-review`.
- **Study:** UTC date and time of the start, then six lowercase hex characters
  (`071121-9e3bba`). Create only the subdirectories you need.
- **Nothing outside a study:** every file sits inside a study directory with a
  `report.md`, so the dashboard's research index lists it. Loose notes under a
  topic or date are not reachable.
- **Corrections** update the same report; a new observation period is a new
  study.

## Collecting and processing data

- Save what a source returned, unchanged, under `data/`, and record for each
  source its endpoint or URL, parameters, and UTC observation time. Write
  derived tables to separate files; never overwrite a raw snapshot.
- Keep the scripts that produced a result in `work/`, runnable from the study
  directory with relative paths, so the result can be reproduced.
- Store only what is not reproducible. Do not keep downloaded packages, wheels,
  virtual environments, caches or build output; record the command and versions
  that produce them instead. Compress large datasets (`.csv.gz`) and mention
  their size in the report.
- Keep secrets and credentials out of every artifact, including logs and raw
  responses.

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
- Studies appear in the dashboard's research index at `/research`, by topic,
  with the frontmatter `title` and `summary`.
- Apply this format to new studies. Existing reports without frontmatter remain
  supported and do not require bulk migration.
