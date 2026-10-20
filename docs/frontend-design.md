# Frontend design conventions

The dashboard (`frontend/`) is React 19 + Vite + Tailwind CSS v4 with theme
tokens defined as CSS custom properties in `src/index.css`. Follow these
conventions so new screens match the existing interface and remain easy to
maintain.

## Visual language

A dense, dark-first trading terminal: "Deep Space + Gold". Flat surfaces separated
by 1px borders, small type, tabular numbers and restrained color. Color carries
meaning (direction, state, primary action), not decoration.

## Theme tokens

Always reference tokens; never hard-code hex values in components. Three themes are
selected by `data-theme` on `<html>` (`hooks/useTheme.ts`): `dark` (default),
`light` and `colorblind` (blue/orange instead of green/red).

| Token | Use |
| --- | --- |
| `--color-bg` | Page ground, sheets, modals |
| `--color-surface` / `--color-surface-hover` | Cards, panels, table rows, list items and their hover |
| `--color-border` / `--color-border-hover` | All dividers and outlines (1px) |
| `--color-text` / `--color-text-muted` | Primary text / labels, secondary values, hints |
| `--color-primary` / `--color-primary-hover` | Gold primary action, selection, focus ring |
| `--on-primary` | Text on a primary (gold) fill. Do not use `text-white` there: it fails contrast in every theme |
| `--color-green` / `--color-red` / `--color-yellow` | Profit/buy, loss/sell/danger, warning |
| `--chart-*` | Chart background, grid, axis text, up/down candles |
| `--chart-series-1..8` | Categorical series in fixed order; never generate extra hues |
| `--chat-inset` / `--chat-rule` | Recessed blocks (code fences, table heads) and hairlines inside chat Markdown |

Tailwind usage spells tokens as arbitrary values, with opacity modifiers for tints:
`text-[var(--color-text-muted)]`, `bg-[var(--color-surface)]`,
`border-[var(--color-border)]`, `bg-[var(--color-red)]/10`,
`border-[var(--color-primary)]/30`. Avoid Tailwind palette colors
(`text-red-400`, `text-amber-400`): they ignore the light and colorblind themes.

## Typography

- Body: "Inter Variable" (self-hosted, imported in `main.tsx`). Do not add font
  families that are not loaded.
- Numbers, addresses, ids and code: `font-mono` (JetBrains Mono) plus
  `tabular-nums` for columns that update.
- Scale: `text-xs` (12px) is the default control and table size; `text-sm` for body
  copy and form fields; `text-[10px]`/`text-[11px]` for dense metadata; `text-lg`
  and larger only for page or KPI headings.
- Section labels and column headers: `text-[10px] uppercase tracking-wider
  text-[var(--color-text-muted)]`.
- Weights: `font-medium` for controls, `font-semibold` for headings and key values,
  `font-bold` sparingly.

## Spacing, shape and depth

- Gaps: `gap-1` to `gap-3`; padding `px-3 py-1.5` (controls), `px-3 py-2`
  (rows and fields), `p-3`/`p-4` (cards and dialogs).
- Radius: `rounded-md` for controls and inputs, `rounded-lg` for cards and
  popovers, `rounded-xl` for modals and sheets, `rounded-full` for pills, badges and
  status dots.
- Depth comes from borders and surface steps. Use `shadow-xl` only for floating
  layers (menus, dialogs).
- Overlays come from `components/ui/dialog`; do not write `fixed inset-0` by hand.
  Modals take `DIALOG_BACKDROP` and `DIALOG_PANEL` (plus the footer and button
  classes): a dimmed, lightly blurred backdrop, a `rounded-2xl` panel that slides in
  (no animation under reduced motion), centred at every width; on phones the footer
  buttons split one row evenly, Cancel left and the action right. A view that takes
  over the whole viewport rather than floating over it (a chart, a report, the editor)
  uses `FULLSCREEN_LAYER`, or `FULLSCREEN_LAYER_TOP` when it must sit above an open
  dialog. Confirmations use `components/ui/ConfirmDialog`: the title is the question,
  the body is at most one line (what it touches, or the one consequence a reader could
  get wrong), then Cancel + action; no icons, no restated warnings. One-line edits such
  as a rename use `components/ui/TextInputDialog`; both portal to `<body>`. Rows never
  turn into inline forms or inline confirms. Floating menus use
  `components/ui/AnchoredMenu`.

## Shared UI primitives

`src/components/ui/` holds the building blocks every feature composes. Reach for
one of these before writing the markup again; when a feature needs a variant, add
a prop here rather than a copy there. `primitives.test.tsx` covers their contracts.

| Primitive | Use it for | Not for |
| --- | --- | --- |
| `Card` / `CARD_CLASS` | A bordered surface panel (`rounded-lg border bg-surface p-4`). `as` picks the element | Table rows, list items, chat bubbles: those are surfaces with their own density |
| `EmptyState` | "Nothing here" in a list, table or detail pane: one muted, centred line | Loading (use `SpinnerBlock`) or an error (use a notice) |
| `Spinner`, `SpinnerBlock`, `FallbackSpinner` | `Spinner` is inline and inherits color (`size`, `tone="muted|primary"`, `label` for a status role); `SpinnerBlock` is a centred, muted block for an empty pane; `FallbackSpinner` is the route `Suspense` fallback | Status dots or progress rings that mean a state rather than "loading" |
| `IconButton` | A button that is only an icon (close, back, copy, refresh, collapse). `label` is required and becomes the accessible name; `size`, `tone="default|primary|danger"` | Buttons with text; toggles whose pressed state matters (write `aria-pressed` yourself) |
| `Disclosure` | A section that opens under a heading: `summary`, chevron, `aria-expanded`/`aria-controls`, body unmounted while closed; controlled or uncontrolled | Tabs, accordions that must keep a hidden body mounted, or rows whose expansion is the row's own layout |
| `Field`, `Label`, `INPUT_CLASS`, `LABEL_CLASS` | Form rows: `Field` wraps `Label`, the control and an optional `hint`; `INPUT_CLASS` is the text input look | Selects (see `SelectField`) |
| `SelectField` (`select-field.tsx`) | Every select; the app has no native `<select>` | — |
| `table.ts` (`TABLE_HEAD_ROW`, `TH`, `TD`, `*_NUM`, `DENSE_*`) | Table head rows, text and numeric cells at the standard and dense pitches | Grids of KPI tiles |
| `dialog.ts`, `ConfirmDialog`, `TextInputDialog`, `InlineConfirm` | Modal chrome and the confirmation patterns described above; `InlineConfirm` only for small in-row toggles | Full-page takeovers that are not dialogs (use `FULLSCREEN_LAYER`) |
| `AnchoredMenu` | A floating menu positioned against a trigger | Select menus (`SelectField` has its own) |
| `HintBubble` | A one-time hint (`hooks/useOneTimeHint.ts` decides when) | Tooltips: use `title` |

Choosing between them:

- A pane that is waiting shows `SpinnerBlock`; a control that is working shows an
  inline `Spinner` beside its label; a pane with nothing to show shows `EmptyState`.
  Never leave the slot blank and never show a zero in place of an absent value.
- An action that only has an icon is an `IconButton`. The moment it gains a word
  it is a button with the classes under *Components and states*.
- Collapsible content under a heading is a `Disclosure`. A chevron drawn next to a
  row that expands into its own detail is the row's business, not a `Disclosure`.
- A panel with a title and padding is a `Card`. A dense list of rows is a bordered
  container with `divide-y`, not a stack of cards.

## Components and states

- Primary button: `rounded-md bg-[var(--color-primary)] px-3 py-1.5 text-xs
  font-medium text-[var(--on-primary)] transition-colors
  hover:bg-[var(--color-primary)]/80 disabled:cursor-not-allowed
  disabled:opacity-50`. Text on a primary fill is always `--on-primary`; red and
  green fills keep `text-white` (as `DIALOG_DANGER` does).
- Primary-tinted (ghost) button: `text-[var(--color-primary)]
  hover:bg-[var(--color-primary)]/10`.
- Secondary button: border + surface, `hover:bg-[var(--color-surface-hover)]`.
- Destructive actions: red text or a red tint (`bg-[var(--color-red)]/10`). Deleting
  records or files is confirmed in `components/ui/ConfirmDialog` (a modal naming
  what is removed); `components/ui/InlineConfirm` is for small in-row toggles.
- Selects: always `components/ui/select-field` (`SelectField`); the app has no native
  `<select>`. Its menu opens directly under the trigger (flipping above only when
  there is no room), is keyboard-navigable, and supports grouped and disabled
  options, a disabled trigger, and `id`/`describedBy` for a `<label htmlFor>`.
  Sizes: `default`, `field` (36px, matches form inputs), `compact`, `small`
  (toolbar), `inline` (frameless, for segmented groups). Tests pick options by
  their `data-value`.
- Notices: `rounded-lg border border-[var(--color-primary)]/30
  bg-[var(--color-primary)]/5 p-3 text-sm` (info), red/yellow equivalents for
  errors and warnings.
- Every interactive element gets `transition-colors`, a hover state, a
  `focus-visible` outline in `--color-primary`, and a disabled state.
- Icons: `lucide-react`, `h-3.5 w-3.5` in compact controls and `h-4 w-4` otherwise,
  `shrink-0` inside flex rows.
- Loading, empty and error states are explicit (`SpinnerBlock`, `EmptyState`, a
  notice); unavailable data is shown as unavailable, never as zero.

## Responsive behavior

- Breakpoints: `md` (768px) separates the mobile layout from desktop; `sm` and `lg`
  refine density. Prefer container queries when a component lives inside resizable
  panes (see `ReportBrowser.tsx`).
- Touch targets: at least 44px on coarse pointers (`pointer-coarse:min-h-11`).
- Form fields render at 16px below 640px so iOS does not zoom on focus (global rule
  in `index.css`).
- Wide tables scroll horizontally inside their own container with a sticky identity
  column; the page itself must not scroll sideways.
- Respect `env(safe-area-inset-bottom)` for content above the mobile navigation.

## Where styles live

1. Tailwind utilities with tokens in the component's `className`. This is the
   default for every component, including responsive and stateful rules.
2. `src/index.css` holds only global rules: theme tokens, the shared
   `.chat-markdown` typography, the 16px form-field rule that stops iOS zooming,
   hidden scrollbars below `md` (scrolling still works), the `scrollbar-none`
   utility for sideways strips, the main navigation's
   edge fade, and the `narrow-pane` custom variant (phones and short landscape
   tablets).
3. There are no feature stylesheets. Do not add files under `src/styles/`.

Patterns that replace feature CSS:

- **Pane-relative layouts:** mark the pane with `@container/<name>` and use
  `@max-[639px]/<name>:` variants (see `ReportBrowser`, `ReportViewer`,
  `WorkspaceSheet`). Use viewport breakpoints (`md:`) only for page-level layout.
- **Rendered content** (Markdown, transcripts): style elements through the
  renderer's component mapping (`research-markdown.tsx`), or with descendant
  variants such as `[&_pre]:whitespace-pre-wrap` on the container.
- **Shared button looks:** export class strings from the feature
  (`routines/routineActionClass.ts`). Build variants from a shared base without
  conflicting color utilities, because Tailwind resolves conflicts by stylesheet
  order, not by class order.
- **DOM hooks for tests and scripts:** use `data-*` attributes, never class names.
- **Compact controls:** `SelectField` (`size="small"`, `fit`) for toolbar menus,
  and `ChartRangeToolbar` for the chart interval and window.

## Repository conventions

Follow the [maintenance workflow](maintenance.md) and keep changes focused on
the behavior being developed:

- Keep the existing import style of each file. New imports may use `@/`, but do
  not rewrite existing relative imports without a reason related to the change.
- Keep related behavior with its owning component or module. Extract files when
  they establish a clear responsibility or support reuse. A screen that outgrows
  one file is split by region into a sibling folder, each region a component with
  an explicit props interface and the state left in the parent (see
  `components/perf/browser/` under `PerfBrowser.tsx`).
- The API client is one module per domain in `lib/api/<domain>.ts`, each exporting
  its `<domain>Api` object and types; `lib/api.ts` holds only `apiFetch`,
  `errorStatus` and `query`. Import the domain module you call, and mock that
  module in tests, rather than a catch-all `api`.
- Name files the way their directory does: PascalCase for components and pages,
  camelCase or kebab-case for `lib/` and `hooks/` modules as the neighbors do.
- Avoid unrelated formatting, ordering, and wording changes.
