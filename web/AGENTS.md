# Frontend conventions (`web/`)

The React app: React 19, Vite, TypeScript (strict), TanStack Query 5, react-router, openapi-fetch, CSS modules over design tokens. Repo-wide rules (commands, commits, gates, contract process, Windows gotchas, sharing the tree with other sessions) are in `../AGENTS.md`; the system and the reasons behind it are in `../ARCHITECTURE.md`. This file covers only how the frontend is built.

## Ownership
- You own `web/**`, except `src/api/openapi.json` and `src/api/schema.ts`. Those are generated from the backend by `just gen-api`. Never edit them by hand. They are ignored by ESLint (`schema.ts`) and Prettier.
- Need an endpoint or field that doesn't exist? Follow the contract process in `../AGENTS.md`, and work around it locally in the meantime.
- Format with `just fmt-web` only.

## Stack and gates
- `just check-web` = `npm run check`: `typecheck` (tsc), `lint` (ESLint, `--max-warnings 0`), `fmt:check` (Prettier), `test` (Vitest).
- ESLint: `strictTypeChecked` + `stylisticTypeChecked` + `jsx-a11y` recommended, with `react-hooks/rules-of-hooks` and `exhaustive-deps` as errors (`eslint.config.js`).
- TypeScript: `strict` plus `noUncheckedIndexedAccess`, `verbatimModuleSyntax` (write `import type` for types), `noUnusedLocals/Parameters`, `noImplicitReturns` (`tsconfig.json`).
- The dev server binds `127.0.0.1:5173` (`strictPort`) and proxies `/api` to `AGENTOS_BACKEND_URL`, which defaults to `http://127.0.0.1:8000` (`vite.config.ts`).
- No component library, no Tailwind, no global store. Server state lives in TanStack Query. Everything else is local state or the URL.

## Layers
```
src/api/       client (api, unwrap, ApiError, Schemas) + queries.ts (every hook and the keys factory)
src/app/       providers, router, shell (Shell, Rail, TopBar, Surface, ThemeMenu), useBoard, keys, motion, theme
src/app/views/ thin route wrappers (Settings, placeholders)
src/domain/    pure selection and vocabulary over server fields (counts, groups, words, actions, stage, format…)
src/ui/        kit primitives: presentational, props in, markup out
src/features/  one folder per feature (queue, compact, ticket, peek, runs, context, pipeline, settings…)
src/styles/    tokens.css, base.css, fonts.css
```
- **Enforced by ESLint:** importing `api` or `unwrap` from `api/client` outside `src/api/` is an error. That is the only import rule. `ApiError`, `Schemas` and `ErrorCode` may be imported anywhere.
- **Convention (not enforced; keep it):**
  - `domain/` is pure: no React and no hooks. It imports only types from `api/client`.
  - `ui/` imports only types (`api/client`, `domain/`) and pure helpers from `domain/`. It never imports `features/`, `app/` or `queries.ts`.
  - A feature may import from another feature (for example `actions` and `peek` are used across views). Anything shared by several features and purely presentational belongs in `ui/`.
  - Each feature keeps its logic in a pure, tested `model.ts` (or a named helper such as `form.ts`, `log.ts`) beside its components. The components stay thin.

## Server access
- All server calls go through hooks in `src/api/queries.ts`. Components never call `fetch`.
- Inside `src/api/`, always resolve calls with `unwrap(api.GET(...))`. It returns `data` on 2xx and otherwise throws `ApiError { status, code, message }`. Never test `if (error)`: an empty-body 502 (proxy up, backend reloading) has a falsy `error` and would pass as success. A non-envelope failure gets the code `http_error`.
- `api` adds `X-AgentOS: 1` to every non-GET/HEAD/OPTIONS request (CSRF; `src/api/client.ts`).
- Types come from the generated schema: `Schemas["TicketSummary"]`, and so on. `ErrorCode` is the backend's closed set.
- Branch on `ApiError.code`, never on message text. Each feature maps codes to user text in one pure, tested function (`features/auth/loginError.ts`, `features/newticket/importForm.ts`, `features/context/errors.ts`, `actionErrorText` in `features/ticket/model.ts`).
- Read app-wide settings (auth mode, Space name, repos, key prefix, runtimes, defaults) from `useConfig()` (`GET /api/config`). Never hardcode them.

## Query keys and invalidation
- There is one hierarchical `keys` factory in `queries.ts`, rooted at `["agentos"]`. A ticket's files, chat and runs hang off `keys.ticket(key)`, so invalidating a ticket refreshes all of it.
- Mutations never patch the cache by hand. Ticket mutations call `useInvalidateTicket`, which refetches the ticket subtree and the board (`keys.tickets()`, `exact: true`), because a mutation can change any server-derived field.
- Settings and Template saves are the exception: they `setQueryData` their own document from the PUT response, then invalidate what depends on it (config, intake, template, the board).
- Login sets `me` and invalidates `keys.all`. Logout removes the ticket cache and sets `me` to `null`.

## Polling
- The server has an SSE stream, but the client doesn't subscribe to it yet, so reads poll with `refetchInterval`. The intervals are constants in `queries.ts`: 3 s for the board and ticket reads, 1 s for an active run and its events, 60 s for Intake. The pending page polls `me` every 15 s. TanStack pauses polling in background tabs.
- When SSE is wired, event → invalidation mapping belongs in `src/api/` next to `keys`, in one place.
- Run events are fetched incrementally (after the last `seq`) and capped at 5,000 per run in memory (`MAX_EVENTS`).

## queryClient behaviour (`src/app/queryClient.ts`)
- `retry: false`: a failed read shows its error at once rather than after three silent retries.
- A 401 on any read, or a 403 `pending_approval` on any read or write, re-checks `keys.me()`. The auth guard then routes to `/login` or swaps the shell to the pending page. `useMe` itself returns `null` on 401.

## Don't derive board state
- Render what the server returns: `attention`, `phase`, `phase_track`, `rail`, `now`, `next_stage`, `active_run_id`, `attention_since`, `latest_run.cancel_requested_at`. Never compute these on the client, because the backend owns the derivation and two copies drift.
- `src/domain/` only selects: it counts, filters, groups and sorts over server fields, and never invents a state. It is pure and unit-tested (`domain/fixtures.ts` builds typed tickets).
- Views read the board through `useBoard()` (`src/app/useBoard.ts`), which returns tickets, `me`, and the shared `counts` and `groups`. Views never count or group for themselves.
- Display helpers (`domain/format.ts`, a feature's `model.ts`) may combine server fields into text. They never derive state.
- Words come from `domain/words.ts` (one vocabulary for every view; sentence case, exactly as shown). Stage glyph and stepper models come from `domain/stage.ts`, which also maps server rail states to drawn states (for example `stale` draws as upcoming).
- If a view needs a state the server doesn't provide, ask for it through the contract process.

## Actions
- `domain/actions.ts` (`ticketActions(ticket, "row" | "header")`) is the one model of which buttons a ticket shows. Queue rows, Compact rows and the Ticket page header all use it. `features/actions/useTicketAction.ts` runs them against the API, so every surface does the same thing for the same button.
- Gate primacy: at most one strong button per group. `you` (blue) for Approve and Complete, `ink` for Retry and a row's Open, `accent` only for Dispatch run on the Ticket page header. The accent never appears on a row.
- "Run from…" lives only in the Pipeline tab.

## Routes and URL state
- Routes are defined in `src/app/router.tsx`. `/login` and `/kit` sit outside the shell and auth guard. Unknown paths redirect to `/`.
- Filter, peek and tab state live in the URL, so views survive a reload and links can be shared:
  - The peek is `?peek=KEY` over any view (`features/peek/usePeek.ts`).
  - Compact's filters are `FILTER_PARAM` (`f`) and `REPO_PARAM` (`repo`) in `features/compact/model.ts`.
  - The Ticket page tab is the `:tab` segment (`TABS` in `features/ticket/model.ts`, Overview by default).
- Change one search param with `withParam` and `withoutParam` (`features/peek/params.ts`), which keep the rest.
- The peek and the Ticket page are separate components. The peek never carries gate actions.

## Drafts and saves
- Editable documents (pipeline, template, settings) use `useDraft` + `SaveBar` (`features/pipeline/`). A draft remembers its base `version` for optimistic concurrency, and `useUnsavedWarning` guards against leaving.
- A page with two drafts passes `{ warn: false }` to each `useDraft` and calls `useUnsavedWarning(a.dirty || b.dirty)` once, because the router allows only one blocker at a time.

## Styling and tokens
- Use CSS modules (`X.module.css` beside `X.tsx`) and `var(--token)`. Raw colour values are allowed only in `src/styles/tokens.css`. Compose class names with `cx` from `ui/`.
- Hues carry one meaning each; the hue rules are in the header of `tokens.css`. Identity hues (a project key's prefix, a person's id) come from `keyHue` / `personHue` in `ui/identity.ts`, a stable hash over the `--cat-*` hues.
- Type: Geist for text and Geist Mono for keys and code. Sentence case everywhere; mono caps only on the YOU pill. Weights are 400, 500 and 600, never 700. The text floor is 11px.
- Code comments cite the design pack as "DESIGN §n". The pack (`../design/agentos-design-pack/`) is local-only and gitignored, so it may be absent in a clone. In that case `tokens.css` and `src/ui/` are the source of truth. Match them rather than inventing new styling.

## Themes and fonts
- Themes use the `data-theme` attribute: `light` (default) and `dark`, with `company-light` / `company-dark` as aliases. The selectors are plain attribute selectors, so a subtree can carry its own theme.
- The inline script in `index.html` applies the saved theme (`localStorage` key `agentos-theme`) before first paint. Keep it in sync with `src/app/theme.ts`.
- Fonts are self-hosted from `@fontsource/geist` and `@fontsource/geist-mono` (`styles/fonts.css`). No Google Fonts request, because the app also runs on networks that block it.

## Kit primitives
- `src/ui/index.ts` is the list of primitives. Import from `../ui` (or the right relative path), and check there before writing a new one.
- `/kit` (`ui/KitSheet.tsx`) renders every primitive in both themes. Update it when you add or change a primitive.

## Motion and keys
- Animate only `transform` and `opacity`. Honour `prefers-reduced-motion` (`base.css` has a global rule; modules with their own motion add one), and `html.ao-paused` (`app/motion.ts`, `base.css`) stops animation while the tab is hidden.
- Hover never opens anything. Click, Enter or Space opens.
- Single-key shortcuts check `isShortcut(e)` (`app/keys.ts`), so they never fire while typing or with a modifier held. Current keys: `/` focuses search, Esc closes, `j`/`k` move between rows in Compact.

## Testing
- Vitest runs in the `node` environment by default, because jsdom is slow to boot. A test that needs a DOM opts in with `// @vitest-environment jsdom` at the top.
- Most logic tests are plain unit tests over `domain/` and feature `model.ts` files. Markup checks can use `renderToStaticMarkup` from `react-dom/server` in node. There is no DOM testing library.
- Tests sit beside the code as `*.test.ts(x)`.

## People
- People are `Person` objects from the API, never bare names or ids. `now.person` / a gate's `person` is a human; `now.actor` is a non-human label (the running stage), so never render an actor as a face.
