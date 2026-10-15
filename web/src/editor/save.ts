// What the save bar says: who saved the document, why Save is off, and what a failed save or load
// tells the user.
import { ApiError, type Schemas } from "../api/client";
import { age, firstName, type Instant } from "../domain/format";

/** What a failed save tells the user, and whether it offers Reload (a 409 `stale_version`). */
export interface SaveProblem {
  text: string;
  reload: boolean;
}

export function describeSaveError(e: unknown): SaveProblem {
  if (e instanceof ApiError) {
    if (e.code === "stale_version") {
      return { text: "Changed by someone else. Reload (your draft will be lost).", reload: true };
    }
    if (e.code === "run_active") {
      return { text: "A run is active; edit the pipeline when it finishes.", reload: false };
    }
    return { text: e.message, reload: false };
  }
  return { text: e instanceof Error ? e.message : "Something went wrong.", reload: false };
}

/**
 * A failed save of a versioned document (`useDocument`). On a 409 `stale_version` its Reload keeps
 * the draft and makes the next save overwrite the other change, so the text says that.
 */
export function describeDocumentError(e: unknown): SaveProblem {
  if (e instanceof ApiError && e.code === "stale_version") {
    return {
      text: "Changed by someone else. Reload keeps your draft; saving it then replaces their change.",
      reload: true,
    };
  }
  return describeSaveError(e);
}

/** The save bar's reason Save is off: "Fix 1 issue first" / "Fix N issues first", or undefined. */
export function fixFirst(issues: number): string | undefined {
  if (issues === 0) return undefined;
  return `Fix ${issues === 1 ? "1 issue" : `${String(issues)} issues`} first`;
}

/** What every versioned document carries besides its value. */
export interface DocumentMeta {
  version: number;
  updated_at: string;
  updated_by: Pick<Schemas["Person"], "display_name"> | null;
}

/** "Saved by Diwash · 5m ago", or "From server config" for a seeded document nobody has saved. */
export function savedLine(doc: DocumentMeta, now: Instant): string {
  if (doc.updated_by === null) return "From server config";
  const when = age(doc.updated_at, now);
  return `Saved by ${firstName(doc.updated_by)} · ${when === "now" ? "just now" : `${when} ago`}`;
}

/** A read that failed because the viewer isn't an admin (the page shows its admins-only line). */
export function isForbidden(e: unknown): boolean {
  return e instanceof ApiError && e.status === 403;
}
