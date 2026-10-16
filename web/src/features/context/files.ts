// Pure helpers for the Context tab's file list. Display only.
import type { Schemas } from "../../api/client";

export type FileInfo = Schemas["FileInfo"];

/** `.claude/skills/x/TASKS.md` → `{dir: ".claude/skills/x", name: "TASKS.md"}`. */
export function splitPath(path: string): { dir: string; name: string } {
  const i = path.lastIndexOf("/");
  return i < 0 ? { dir: "", name: path } : { dir: path.slice(0, i), name: path.slice(i + 1) };
}

/** TASKS.md is the plan the loop edits between a gate and Approve. */
export function isTasksFile(path: string): boolean {
  return splitPath(path).name === "TASKS.md";
}

function rank(path: string): number {
  if (isTasksFile(path)) return 0;
  return path.includes("/") ? 2 : 1;
}

/** TASKS.md first, then root files (AGENTS.md, CLAUDE.md), then the rest; each group by path. */
export function sortFiles<T extends { path: string }>(files: readonly T[]): T[] {
  return [...files].sort((a, b) => {
    const r = rank(a.path) - rank(b.path);
    if (r !== 0) return r;
    return a.path < b.path ? -1 : a.path > b.path ? 1 : 0;
  });
}

/** Who wrote the current version: a person, else the run that synced it back. */
export function authorLabel(f: Pick<FileInfo, "author" | "author_run_id">): string {
  if (f.author) return f.author.display_name;
  if (f.author_run_id !== null) return `run #${String(f.author_run_id)}`;
  return "template";
}
