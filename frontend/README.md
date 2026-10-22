# Condor frontend

The dashboard uses React, TypeScript, Vite, and Vitest. Run these commands from
the `frontend/` directory:

```sh
npm run dev
npm test
npm run lint:ci
npm run build
```

From the repository root, `make build` builds the dashboard and swaps the bundle
into `frontend/dist`. For validation on a checkout serving a dashboard, use a
separate output directory as described in the [maintenance guide](/docs/maintenance.md).

## File ownership

```text
src/
├── components/       # UI and component-owned implementation details
│   └── ui/           # Shared UI primitives
├── hooks/            # Shared React hooks
├── lib/              # Shared non-UI modules, API clients, state, and contracts
├── pages/            # Route-level screens
├── assets/           # Imported static assets
└── tests/            # Tests mirroring the source hierarchy
    ├── components/
    ├── hooks/
    ├── lib/
    ├── pages/
    └── support/      # Shared doubles, fixtures, and test-only helpers
```

Place a test for `src/components/chat/ChatMessage.tsx` at
`src/tests/components/chat/chat-message.test.tsx`. Scenario-specific suites can
use a suffix such as `chat-message.chart-identity.test.tsx`. Keep existing source
module names when moving their tests; new test filenames use kebab-case.

Use the configured `@/` alias for imports across directories, including imports
from tests into source modules. Keep short relative imports inside the same
module. Component-private hooks and helpers stay with their owner; promote
shared code to `hooks/`, `lib/`, or `components/ui/` when it has multiple owners.
Inspect existing implementations before adding another helper or directory.

Domain API clients live under `lib/api/`; `lib/api.ts` owns the shared JSON
transport. Keep specialized error handling and file-response behavior with the
relevant client. Market query hooks live under `hooks/market/`, while their
cache keys belong to the shared query module. LP data lives under `lib/dex/`;
DEX presentation helpers remain with the DEX components. Chat transcript types
and display-currency state live in `lib/`, shared by hooks and non-React readers.

ESLint prevents `lib/` and shared `hooks/` from importing component or page
modules, and prevents `lib/` from importing hooks. Shared types follow their
data or hook owner. Shared chart rendering hooks belong to `components/charts/`.

## Testing

Vitest discovers `*.test.ts`, `*.test.tsx`, `*.spec.ts`, and `*.spec.tsx` under
`src/tests/`. ESLint rejects tests and parallel test directories elsewhere under
`src/`; `npm run lint:ci` enforces that rule in CI.

The default test environment is Node. DOM tests opt in using
`/** @vitest-environment jsdom */`. The `lightweight-charts` resolver alias is
test-only and points to `src/tests/support/lightweight-charts-double.ts`; production
builds use the real library.

Run a focused suite with:

```sh
npm test -- src/tests/components/chat/chat-message.chart-identity.test.tsx
```

Backend Python tests remain in the repository-root `tests/` directory and run
with `uv run --locked pytest`. Shared Python doubles and fixtures live in
`tests/support/`; test modules do not import helpers from other test modules.
