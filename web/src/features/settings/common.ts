// Shared by the admin Settings pages (Space, Intake, Runners): the "who saved this" line and the
// admin gate. Each page edits one or two Settings documents with `useDraft` + `SaveBar`
// (features/pipeline), the same way the Template page does.
import { ApiError } from "../../api/client";
import { age, firstName, type Instant } from "../../domain/format";

/** What every Settings document carries besides its fields. */
export interface SettingsMeta {
  version: number;
  updated_at: string;
  updated_by: { display_name: string } | null;
}

/** "Saved by Diwash · 5m ago", or "From server config" for a seeded document nobody has saved. */
export function savedLine(doc: SettingsMeta, now: Instant): string {
  if (doc.updated_by === null) return "From server config";
  const when = age(doc.updated_at, now);
  return `Saved by ${firstName(doc.updated_by)} · ${when === "now" ? "just now" : `${when} ago`}`;
}

/** A GET that failed because the viewer isn't an admin (the page shows the admins-only line). */
export function isForbidden(e: unknown): boolean {
  return e instanceof ApiError && e.status === 403;
}
