# Agent Instructions

## Project scope

- This repository serves the owner's personal use. Prioritize daily workflows, reliability, privacy, and the owner's concrete experience.
- Do not add public issue intake, community contribution workflows, or support services. Expand scope only for an explicit personal need.

## Project language

- Write maintained documentation, comments, templates, and agent instructions in English. Preserve required localized content and language-specific test fixtures.
- Describe current behavior and reusable constraints concisely. Keep incident narratives and run-specific evidence in research artifacts.

## Repository maintenance

- Follow [the maintenance workflow](docs/maintenance.md) for development, publication, deployment, and recovery.
- `origin` is `rehuony/condor`; `main` is the personal development and release branch. Each new change gets its own focused commit; publish with a normal push. Preserve published history unless the user explicitly requests a history rewrite for a specific operation. Such a request does not change the default for later work.
- One agent owns Git writes. Validate changes before publishing and honor the request's scope for commits, pushes, deployment, and service restarts.

## Research artifacts

- Keep generated reports, datasets, and execution evidence in ignored `.condor/research/`, following the [artifact standard](agents/_defaults/research-artifacts.md) and [report template](agents/_defaults/research-report.md). Source code, tests, and maintained documentation use their normal locations.
- Pass the allocated directory and standard to delegated researchers. Preserve contents and relative references when moving existing artifacts.
- Share descriptive `/research/<relative-path>/report.md` links, without local absolute paths in labels or targets.
