# Frontend conventions (`web/`)

The React app: React 19, Vite, TypeScript (strict), TanStack Query 5, react-router, openapi-fetch, CSS modules over design tokens. Repo-wide rules (commands, commits, gates, contract process, Windows gotchas, sharing the tree with other sessions) are in `../AGENTS.md`; the system and the reasons behind it are in `../ARCHITECTURE.md`. This file covers only how the frontend is built.

## Ownership
- You own `web/**`, except `src/api/openapi.json` and `src/api/schema.ts`. Those are generated from the backend by `just gen-api`. Never edit them by hand. They are ignored by ESLint (`schema.ts`) and Prettier.
- Need an endpoint or field that doesn't exist? Follow the contract process in `../AGENTS.md`, and work around it locally in the meantime.
- Format with `just fmt-web` only.

## Stack and gates
- `just check-web` = `npm run check`: `typecheck` (tsc), `lint` (ESLint, `--max-warnings 0`), `fmt:check` (Prettier), `check:spacing` (no raw spacing lengths in CSS modules; see Styling), `test` (Vitest). tsc and ESLint cover `scripts/` too.
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
src/ui/        kit primitives: presentational, props in, markup out (plus text utilities, StatusLine, useNow)
src/editor/    the editor kit: fields, drafts, the versioned-document hook, DocumentPage, ListEditor, issues, save bar
src/features/  one folder per feature (queue, compact, ticket, peek, runs, context, pipeline, settings…)
src/styles/    tokens.css, base.css, fonts.css
```
- **Enforced by ESLint:** importing `api` or `unwrap` from `api/client` outside `src/api/` is an error. That is the only import rule. `ApiError`, `Schemas` and `ErrorCode` may be imported anywhere.
- **Convention (not enforced; keep it):**
  - `domain/` is pure: no React and no hooks. It imports only types from `api/client`.
  - `ui/` imports only types (`api/client`, `domain/`) and pure helpers from `domain/`. It never imports `features/`, `app/` or `queries.ts`.
  - `editor/` imports `ui/`, `domain/`, `ApiError` and types from `api/client`, TanStack Query and react-router. It never imports `features/`, `app/` or `api/queries.ts`: pages pass their queries and mutations in. Components import the barrel (`editor/index.ts`); a pure `model.ts` imports `editor/save`, `editor/issues` or `editor/list` directly, so its node tests don't pull in React or CSS.
  - A feature may import from another feature (for example `actions` and `peek` are used across views). Anything shared by several features and purely presentational belongs in `ui/`.
  - Each feature keeps its logic in a pure, tested `model.ts` (or a named helper such as `form.ts`, `log.ts`) beside its components. The components stay thin.

## Server access
- All server calls go through hooks in `src/api/queries.ts`. Components never call `fetch`.
- Inside `src/api/`, always resolve calls with `unwrap(api.GET(...))`. It returns `data` on 2xx and otherwise throws `ApiError { status, code, message, issues }`. Never test `if (error)`: an empty-body 502 (proxy up, backend reloading) has a falsy `error` and would pass as success. A non-envelope failure gets the code `http_error`.
- `api` adds `X-AgentOS: 1` to every non-GET/HEAD/OPTIONS request (CSRF; `src/api/client.ts`).
- Types come from the generated schema: `Schemas["TicketSummary"]`, and so on. `ErrorCode` is the backend's closed set.
- Branch on `ApiError.code`, never on message text. Each feature maps codes to user text in one pure, tested function (`features/auth/loginError.ts`, `features/newticket/importForm.ts`, `features/context/errors.ts`, `actionErrorText` in `features/ticket/model.ts`).
- A 422 `validation_error` carries every problem in `ApiError.issues` (`{loc, message}`, `loc` relative to the request body). Place them with `editor/issues.ts`; never parse `message`.
- Read app-wide settings (auth mode, Space name, repos, key prefix, runtimes, defaults) from `useConfig()` (`GET /api/config`). Never hardcode them.
- Settings documents (space, repos, intake, runner, mappings) are one typed map, `SettingsDocuments` (area → its read and value types; `DocumentArea`, `SettingsRead<A>`, `SettingsValue<A>`), with generic hooks: `useSettings(area, enabled)`, `useSaveSettings(area)` (`{value, base_version}`) and `useValidateSettings(area)` (`{value}`; resolves on 204, throws the 422). Runtimes keep `useRuntimeSettings` / `useSaveRuntimeSettings`. The default workflow moves with `useSetDefaultWorkflow`, not a Space save.
- A settings page holds no copy of a server rule. Anything that refuses, caps or reports (patterns, lengths, limits, duplicates, cross-field rules) comes from the server's issues, live through the validate-only call. Inputs that never refuse anything (upper-casing a key as it is typed, `""` ↔ `null`) may stay. The runtime rules in `settings/runners/model.ts` are the remaining exception until the runtime editor is rebuilt.

## Query keys and invalidation
- There is one hierarchical `keys` factory in `queries.ts`, rooted at `["agentos"]`. A ticket's files, chat and runs hang off `keys.ticket(key)`, so invalidating a ticket refreshes all of it.
- Mutations never patch the cache by hand. Ticket mutations call `useInvalidateTicket`, which refetches the ticket subtree and the board (`keys.tickets()`, `exact: true`), because a mutation can change any server-derived field.
- Settings and Template saves are the exception: they `setQueryData` their own document from the PUT response, then invalidate what depends on it (config, intake, template, the board). For the settings documents that is the `savedRefetches` map in `queries.ts`, one commented entry per area.
- Login sets `me` and invalidates `keys.all`. Logout removes the ticket cache and sets `me` to `null`.

## Polling
- Reads poll with `refetchInterval`. The intervals are constants in `queries.ts`: 3 s for the board and ticket reads, 1 s for an active run and its events, 60 s for Intake. The pending page polls `me` every 15 s. TanStack pauses polling in background tabs.
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

## Editor kit (`src/editor/`)
What every editable page shares. `editor/index.ts` lists it; check there before writing an editor part. The import rule is under Layers.
- **`useDocument({ query, save, validate?, toForm?, fromForm?, same?, keys?, shown? })`** edits one versioned document (`{value, version, updated_at, updated_by}` plus the area's read-only extras). The page passes its query and mutations in.
  - Load and draft: the draft sits on `useDraftState`, which remembers the version it started from. A draft equal to the saved copy is dropped, so the next edit starts from the current version.
  - Save sends `{value, base_version}`. On a 409 `stale_version` the save bar offers Reload, which refetches and keeps the draft on the fresh version, so the next save replaces the other admin's change on purpose. Discard drops the draft and shows the other admin's copy.
  - Issues come from the last save's 422 or from the validate-only call, which runs `VALIDATE_DELAY_MS` (400 ms) after a dirty draft stops changing. They show only while the draft still equals what was checked; a save outdates a validate still in flight.
  - `toForm(value, read)` / `fromForm(form)` convert between the document's value and the form the page edits (keyed rows, `""` for null); keep them at module level so they are stable. `keys(form)` gives the row keys of the form's lists, read when the form is sent; `shown` lists the field paths the page draws. Both go to the issues module.
  - The hook holds no leave warning: `DocumentPage` does.
- **`DocumentPage`** draws an admin page over `sources` (documents or plain query results): "Loading {noun}" while the viewer or a source loads, the `adminsOnly` line for a non-admin viewer or a 403 from any source, "Can't load {noun} · …" on another failure, then an optional header (`title`, `meta`), a `description` note and the page. It holds the page's one leave warning, over every source's `dirty` plus its own `dirty` prop for a plain `useDraftState` draft on the same page (the runtimes on Runners). Never put a `useDraft` under it, not even with `warn: false`: that still registers a router blocker, a child registers after the page, and react-router consults only the last blocker, so the shared warning would never fire. Users uses DocumentPage with no document and no save bar (each change applies at once).
- **`DocumentSection`** draws one document: the save bar (saved-by line, unsaved marker, Discard / Save, "Fix N issues first", the save problem with Reload, form-level issues) above `children(form, read)`. A page with two documents stacks two sections, each saved on its own.
- **`ListEditor`** is a controlled list whose rows carry a stable `key` (`list.ts`: `keyed`, `unkeyed`, `newKey`, `keysOf`, `moveRow`), so a row keeps its state and its issues across moves. The page renders each row's fields (`children`) and optional `details`; the kit draws the chrome and names it from `label(row)` ("Move repo r up", "Remove repo r"):
  - Add focuses the new row's first field.
  - Remove asks inline (`confirmText`, Escape cancels), except for an `isBlank` row.
  - An `ordered` list gets row numbers and Move up / down, with focus kept on the moved row (on its other arrow at an end).
  - `details` gives each row an expand toggle.
  - `min` / `max` turn Remove / Add off. Settings pages pass none: a list over the server's limit shows the server's issue through `issue`.
- **`usePreview(run, input, same?)`** is a live preview ("Test query", "Try it") for one input. Its result, error and pending state show only while the input still equals what ran, and only the latest run counts. It is a hook, so call it inside a per-row component.
- **Fields** (`fields.tsx`): `Field` draws the label and the hint or error and calls its child with `{ id, describedBy, invalid }`; pass them to the control's `id`, `aria-describedby` and `aria-invalid`, so the label names the control and the note is its description. `Input` (`mono` for ids, branches and commands), `Textarea`, `Select` and `Toggle` are the native controls in kit styles; `fieldStyles` holds the raw classes.
- **Issues** (`issues.ts`, pure): `placeIssues(issues, { root, keys, shown })` (or `issuesOf(error, …)`) turns a 422's issues into dotted field paths below `root` (default `["value"]`), such as `repos.<rowKey>.url`. List indices become the row keys of the snapshot that was sent, so an issue stays on its row after a move. An issue lands on the deepest `shown` path that contains it (`*` matches one segment); one outside the snapshot or on no shown path is form level. Read them with `errorAt` (a Field's error), `messagesAt` (a list's own issues), `countWithin` (a collapsed row) and `issueCount`. Row keys never contain a dot.
- **Save helpers** (`save.ts`, pure): `describeSaveError` / `describeDocumentError` (what a failed save says and whether it offers Reload), `fixFirst(n)`, `savedLine(doc, now)` ("Saved by … · 5m ago", or "From server config" for a seeded document) and `isForbidden`. `SaveBar` and `pageStyles` are the parts the editors not yet on `useDocument` compose by hand.
- The kit's behaviour is pinned by `DocumentPage.test.tsx` and `ListEditor.test.tsx` (jsdom, by role and name) and the pure tables in `issues.test.ts` and `save.test.ts`. A page built on the kit gets no test of its own unless it has behaviour of its own.

## Drafts and saves
- Settings documents (Space, repos, runner, intake queries, field mappings) are edited with `useDocument` under `DocumentPage` / `DocumentSection`.
- The pipeline (Pipeline tab), template (Space defaults) and workflow editors still use `useDraft` + `SaveBar` wired by hand until they are rebuilt on the kit, and the runtimes (on the Runners page, under `DocumentPage`) a `useDraftState` + `SaveBar`. Their Reload drops the draft and refetches.
- `useDraft(saved, version, same)` is `useDraftState` plus its own `useUnsavedWarning`. A draft remembers its base `version` for optimistic concurrency (409 `stale_version`), and a draft equal to the saved copy starts over from the current version.
- One leave warning per page, because react-router allows one blocker at a time (it consults the last one registered). Under `DocumentPage`, a second draft is a `useDraftState` passed in as `dirty`. A page without it that has two drafts passes `{ warn: false }` to each `useDraft` and calls `useUnsavedWarning(a.dirty || b.dirty)` once, after both.
- The warning covers closing the tab and path changes; a query-string change (a peek opening) never asks.

## Styling and tokens
- Use CSS modules (`X.module.css` beside `X.tsx`) and `var(--token)`. Raw colour values are allowed only in `src/styles/tokens.css`. Compose class names with `cx` from `ui/`.
- Spacing (margin, padding, gap) uses the `--sp-*` scale in `tokens.css` (`--sp-1` 2px to `--sp-12` 36px), `0`, `auto`, `%`, or `calc()` / `min()` over tokens; an off-scale value takes the nearest token. `npm run check:spacing` (`scripts/check-spacing.ts`, part of `npm run check`) scans every `src/**/*.module.css` and fails on a raw px, em or rem length in a margin, padding or gap declaration, including inside `calc()`, `min()` and `var()` fallbacks (comments are ignored). Its `ALLOWED` list names exact declarations, each with a one-line reason (1px hairlines, the stepper's 1.5px diamond margin); an entry that no longer matches fails the check too. `scripts/check-spacing.test.ts` tests its matcher.
- Hues carry one meaning each; the hue rules are in the header of `tokens.css`. Identity hues (a project key's prefix, a person's id) come from `keyHue` / `personHue` in `ui/identity.ts`, a stable hash over the `--cat-*` hues.
- Type: Geist for text and Geist Mono for keys and code. Sentence case everywhere; mono caps only on the YOU pill. Weights are 400, 500 and 600, never 700. The text floor is 11px.
- Surfaces: one recessed background per view. Rows that are in flight or need action are raised (`--row-raised*`); settled rows (done, closed) are flat (`--row-flat*`). My queue and Compact rows carry a 3px status edge, and only Needs-you rows get `--you-wash`.
- Repos get no colour (one repo can hold many projects). Talk's faces keep their own `--av-*` palette.
- Code comments cite the design pack as "DESIGN §n". The pack (`../design/agentos-design-pack/`) and its later visual refresh are local-only, so they may be absent in a clone; the refresh overrides the pack's row surfaces, hover, focus, weights, stepper and colours. In that case `tokens.css` and `src/ui/` are the source of truth. Match them rather than inventing new styling.

## Themes and fonts
- Themes use the `data-theme` attribute: `light` (default) and `dark`, with `company-light` / `company-dark` as aliases. The selectors are plain attribute selectors, so a subtree can carry its own theme.
- The inline script in `index.html` applies the saved theme (`localStorage` key `agentos-theme`) before first paint. Keep it in sync with `src/app/theme.ts`.
- Fonts are self-hosted from `@fontsource/geist` and `@fontsource/geist-mono` (`styles/fonts.css`). No Google Fonts request, because the app also runs on networks that block it.

## Kit primitives
- `src/ui/index.ts` is the list of primitives. Import from `../ui` (or the right relative path), and check there before writing a new one.
- `/kit` (`ui/KitSheet.tsx`) renders every primitive in both themes. Update it when you add or change a primitive.
- Text utilities: `text` (`ui/text.module.css`) has `label`, `meta`, `error` and `ellipsis` on the type tokens. Compose them with `cx` (`cx(text.meta, text.ellipsis)`) instead of a module's own label, note or error class.
- `StatusLine` is the one loading, permission and error line: a sentence in `--ink-3` (`error`: `--blocked`, announced as an alert), `inset` when it stands alone in a view. No spinners, no toasts.
- `useNow(intervalMs)` is the clock for ages and saved-by lines.

## Motion and keys
- Animate only `transform` and `opacity`. Honour `prefers-reduced-motion` (`base.css` has a global rule; modules with their own motion add one), and `html.ao-paused` (`app/motion.ts`, `base.css`) stops animation while the tab is hidden.
- Hover never opens anything. Click, Enter or Space opens.
- Hover on Compact and Intake rows lights them up (`--row-hover*`) and never moves them. My queue items lift by `--lift` (transform only). Focus looks like hover plus a 2px `--focus` (ink) outline at a 2px offset.
- Single-key shortcuts check `isShortcut(e)` (`app/keys.ts`), so they never fire while typing or with a modifier held. Current keys: `/` focuses search, Esc closes, `j`/`k` move between rows in Compact.

## Testing
- Vitest runs in the `node` environment by default, because jsdom is slow to boot. A test that needs a DOM opts in with `// @vitest-environment jsdom` at the top.
- Most logic tests are plain unit tests over `domain/` and feature `model.ts` files. Markup checks can use `renderToStaticMarkup` from `react-dom/server` in node. There is no DOM testing library.
- Tests sit beside the code as `*.test.ts(x)`.
- Test logic, not wording: assert behaviour (values, branches, which state or code is chosen), not full UI sentences, which only break on intended rewording. The exception is the error-code → text functions, where the text is the point; tsc against the generated schema and browser checks cover the rest of the UI.
- Test behaviour, requirements or tricky algorithms only. An error test checks the code plus at most one crucial word, never a sentence.
- UI tests find elements by role and accessible name, not by sentences of copy.
- Comments describe what the code does now, never which ticket or request added it.
- Browser checks with the Playwright MCP share one browser between agents. Open your own tab, put `page.route` fakes only on that tab, and never call `unrouteAll`. Switch themes with `document.documentElement.dataset.theme`, not the theme menu, which writes `localStorage` for every tab. Never save real settings or change real tickets from a check; fake the writes with `page.route`.

## People
- People are `Person` objects from the API, never bare names or ids. `now.person` / a gate's `person` is a human; `now.actor` is a non-human label (the running stage), so never render an actor as a face.

## Not built on purpose
- Never: presence, online dots or typing indicators.
- Not in our model, so not drawn even where the design pack shows them: watchers and a "Watching" rack, reviewers, reactions, a runner pool gauge, queue positions, runner names, acceptance criteria (the TASKS.md plan stands in for them).
- Not yet: the Activity tab, Context diff, the Manifest view, row glides.
