# Agent Instructions

## Project scope

- This repository serves the owner's personal use. Prioritize daily workflows, reliability, privacy, and the owner's concrete experience.
- Do not add public issue intake, community contribution workflows, or support services. Expand scope only for an explicit personal need.

## Project language

- Write maintained documentation, comments, templates, and agent instructions in English. Preserve required localized content and language-specific test fixtures.
- Describe current behavior and reusable constraints concisely. Keep incident narratives and run-specific evidence in research artifacts.
- Name repository files by their path from the repository root: root-relative links (`/docs/maintenance.md`) in documentation and code-span paths (`agents/_defaults/research-artifacts.md`) in agent instructions. Do not write `../` chains; a skill copied into `.condor/agents/` would resolve them against the wrong directory.

## Repository maintenance

- Follow [the maintenance workflow](docs/maintenance.md) for development, publication, deployment, and recovery.
- `origin` is `rehuony/condor`; `main` is the personal development and release branch. Each new change gets its own focused commit; publish with a normal push. Preserve published history unless the user explicitly requests a history rewrite for a specific operation. Such a request does not change the default for later work.
- One agent owns Git writes. Validate changes before publishing and honor the request's scope for commits, pushes, deployment, and service restarts.
- Operational scripts live in `scripts/shell/` behind one-line Makefile targets ([scripts/README.md](scripts/README.md)); keep the repository root free of them. Scripts read `.env` as data and never source it. When changing installation or deployment, run `bash -n` and `shellcheck -S error` on the scripts and check every caller, test, and document that names them.

## Research artifacts

- Keep generated reports, datasets, and execution evidence in ignored `data/research/`, following the [artifact standard](agents/_defaults/research-artifacts.md) and [report template](agents/_defaults/research-report.md). Source code, tests, and maintained documentation use their normal locations.
- Pass the allocated directory and standard to delegated researchers. Preserve contents and relative references when moving existing artifacts.
- Share descriptive `/research/<relative-path>/report.md` links, without local absolute paths in labels or targets.

## File ownership

- Keep frontend tests in `frontend/src/tests/`, mirroring the source directory hierarchy. Use kebab-case test filenames; shared test doubles and fixtures belong in `frontend/src/tests/support/`. Do not create a parallel `test/`, `__tests__/`, or colocated test tree.
- Put shared UI in `components/ui/`, shared hooks in `hooks/`, and shared non-UI contracts and helpers in `lib/`. Keep private component code with its owner. Inspect existing public entries before adding another implementation or moving code across these boundaries.
- Domain API clients belong in `lib/api/`; preserve their response and error contracts when consolidating ownership. Shared data and hooks must not import UI modules, and `lib/` must not import hooks; ESLint enforces this dependency direction.
- Keep backend tests in the repository's `tests/`; their Python runner and fixtures are independent of the frontend suite. Put each test module in the package for the area it covers (`agents`, `dex`, `infra`, `market_data`, `mcp`, `reports`, `routines`, `runtime`, `telegram`, `trading`, `web`), not directly under `tests/`; tests that need the repository root use `Path(__file__).resolve().parents[2]`. Shared Python doubles and fixture factories belong in `tests/support/`, not in another test module. Keep pytest registration and suite-wide isolation in `conftest.py`. See [the frontend guide](frontend/README.md) for test commands and ownership examples.
