// Fails when a CSS module spaces with a raw length instead of the spacing tokens.
//
// Every margin, padding and gap declaration in `src/**/*.module.css` uses `0`, `auto`, `%`,
// `var(--…)` (the `--sp-*` scale in `src/styles/tokens.css`) or `calc()` / `min()` over those.
// A raw px, em or rem length is a violation unless ALLOWED lists the exact declaration.
// Comments are ignored. Run from `web/` with `node scripts/check-spacing.ts` (part of `npm run check`).

import { readdirSync, readFileSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

export interface Allowed {
  file: string; // path from web/, with forward slashes
  declaration: string; // "property: value", whitespace collapsed
  reason: string;
}

export interface Violation {
  file: string;
  line: number;
  declaration: string;
}

export const ALLOWED: readonly Allowed[] = [
  {
    file: "src/ui/Tabs.module.css",
    declaration: "margin-bottom: -1px",
    reason: "the active tab's underline overlaps the list's 1px bottom rule",
  },
  {
    file: "src/features/talk/Talk.module.css",
    declaration: "margin: -1px",
    reason: "the visually hidden (screen reader only) pattern",
  },
  {
    file: "src/features/talk/Talk.module.css",
    declaration: "margin-bottom: 1px",
    reason: "lines the Send button up with the textarea's bottom edge",
  },
  {
    file: "src/features/ticket/BaseBranchField.module.css",
    declaration: "margin: -1px 0",
    reason: "cancels the input's 1px border so editing keeps the line height",
  },
  {
    file: "src/ui/Age.module.css",
    declaration: "margin-left: 1px",
    reason: "hairline between an age's number and its unit",
  },
  {
    file: "src/ui/StageGlyph.module.css",
    declaration: "margin: 0 1px",
    reason: "room for the rotated gate diamond's corners",
  },
  {
    file: "src/ui/Stepper.module.css",
    declaration: "margin: 3.5px 0",
    reason: "centres the 3px bar on the stepper's 10px track",
  },
  {
    file: "src/ui/Stepper.module.css",
    declaration: "margin: 4.25px 0",
    reason: "centres the 1.5px skipped bar on the stepper's 10px track",
  },
  {
    file: "src/ui/Stepper.module.css",
    declaration: "margin: 1px 0",
    reason: "centres the 8px gate diamond on the stepper's 10px track",
  },
  {
    file: "src/ui/Stepper.module.css",
    declaration: "padding: 0 1px",
    reason: "room for a bare gate diamond's corners",
  },
  {
    file: "src/features/pipeline/PipelineEditor.module.css",
    declaration: "margin: 0 1px",
    reason: "room for the rotated gate diamond's corners",
  },
  {
    file: "src/features/context/ContextTab.module.css",
    declaration: "padding: 1px var(--sp-2)",
    reason: "a 1px vertical pad keeps the tag one text line tall",
  },
  {
    file: "src/features/runs/RunsTab.module.css",
    declaration: "padding: 1px var(--sp-3)",
    reason: "a 1px vertical pad keeps the verdict pill one text line tall",
  },
];

const SPACING_PROPERTY =
  /^(?:margin|padding)(?:-(?:top|right|bottom|left)|-(?:inline|block)(?:-(?:start|end))?)?$|^(?:grid-)?(?:row-|column-)?gap$/;

// A number with a length unit, not part of a name such as `--sp-10`.
const RAW_LENGTH = /(?<![\w.-])-?(?:\d+\.?\d*|\.\d+)(?:px|r?em)\b/gi;

// `property: value` up to the next `;` or `}`; a selector's `:hover {` never matches.
const DECLARATION = /(?<=^|[{;\s])(-{0,2}[a-zA-Z][\w-]*)\s*:\s*([^;{}]*[^;{}\s])\s*(?=[;}])/g;

export function isSpacingProperty(property: string): boolean {
  return SPACING_PROPERTY.test(property.toLowerCase());
}

/** The non-zero raw lengths in a value, in order. */
export function rawLengths(value: string): string[] {
  return [...value.matchAll(RAW_LENGTH)].map((m) => m[0]).filter((len) => parseFloat(len) !== 0);
}

/** Blanks out comments, keeping newlines so offsets and line numbers stay put. */
export function maskComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, (comment) => comment.replace(/[^\n]/g, " "));
}

/** Every spacing declaration in one file that holds a raw length. */
export function findViolations(file: string, css: string): Violation[] {
  const masked = maskComments(css);
  const found: Violation[] = [];

  for (const m of masked.matchAll(DECLARATION)) {
    const [, property = "", value = ""] = m;
    if (!isSpacingProperty(property) || rawLengths(value).length === 0) continue;

    found.push({
      file,
      line: masked.slice(0, m.index).split("\n").length,
      declaration: `${property}: ${value.replace(/\s+/g, " ")}`,
    });
  }

  return found;
}

/** Violations not on the allowlist, and allowlist entries that no longer match anything. */
export function check(
  files: readonly { file: string; css: string }[],
  allowed: readonly Allowed[],
): { violations: Violation[]; unused: Allowed[] } {
  const used = new Set<Allowed>();
  const violations: Violation[] = [];

  for (const { file, css } of files) {
    for (const v of findViolations(file, css)) {
      const entry = allowed.find((a) => a.file === file && a.declaration === v.declaration);
      if (entry) used.add(entry);
      else violations.push(v);
    }
  }

  return { violations, unused: allowed.filter((a) => !used.has(a)) };
}

function moduleFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true, recursive: true })
    .filter((e) => e.isFile() && e.name.endsWith(".module.css"))
    .map((e) => join(e.parentPath, e.name))
    .sort();
}

function main(): number {
  const web = fileURLToPath(new URL("..", import.meta.url));
  const files = moduleFiles(join(web, "src")).map((path) => ({
    file: relative(web, path).split(sep).join("/"),
    css: readFileSync(path, "utf8"),
  }));

  const { violations, unused } = check(files, ALLOWED);

  for (const v of violations) console.error(`${v.file}:${String(v.line)}: ${v.declaration}`);
  for (const a of unused)
    console.error(`${a.file}: allowlist entry matches nothing: ${a.declaration}`);

  if (violations.length > 0 || unused.length > 0) {
    console.error(
      `check-spacing: ${String(violations.length)} raw spacing length(s); use the --sp-* tokens (src/styles/tokens.css).`,
    );
    return 1;
  }

  console.log(`check-spacing: ${String(files.length)} CSS modules on spacing tokens.`);
  return 0;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) process.exitCode = main();
