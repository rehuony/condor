# Agent Instructions

## Research artifacts

- Store generated research reports, market data snapshots, analysis results, and checkpoints under `.condor/research/`, grouped by topic and date when useful.
- Do not write these artifacts to the repository root or to Git-tracked directories. Keep them out of the Git index; `.condor/` is already ignored.
- Include this storage rule and the destination directory when delegating research to another agent.
- When relocating existing research files, preserve their contents and keep relative references between the files valid.
- Source code, tests, and maintained project documentation continue to follow the project's normal directory structure.
- For new research, follow [the research artifact standard](agents/_defaults/research-artifacts.md) and start from [the report template](agents/_defaults/research-report.md).
- Use `.condor/research/<topic>/<YYYY-MM-DD>/<HHmmss>-<short-id>/report.md` as the entry point; keep datasets in `data/`, images in `assets/`, and reproducibility material in `work/`. Create only the directories needed.
- Share `/research/<relative-path>/report.md` links with descriptive titles. Never expose the machine's absolute paths in link targets or labels.
