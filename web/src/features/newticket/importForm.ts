// "Import from Jira" in the New ticket dialog: manual take-in by key or link (SPEC §17,
// POST /api/tickets/import). Pure: whether to offer it, what to send, and the error words.
import { ApiError, type Schemas } from "../../api/client";

/** Offer the import row only when the server can import from Jira (`AppConfig.sources`). */
export function offersJiraImport(sources: Readonly<Schemas["AppConfig"]["sources"]>): boolean {
  return sources.includes("jira");
}

/** The request body, or null for a blank ref (which doesn't submit). The server parses the ref. */
export function toImportBody(ref: string): Schemas["ImportRequest"] | null {
  const trimmed = ref.trim();
  return trimmed ? { ref: trimmed } : null;
}

/** A failed import, in words for the line under the row. */
export function importErrorText(e: unknown): string {
  if (!(e instanceof ApiError)) return e instanceof Error ? e.message : "Something went wrong";
  if (e.status === 404) return "Jira can't find that issue, or your account can't see it.";
  switch (e.code) {
    case "key_taken":
      return "Another ticket here already uses that issue's key, so it can't be imported.";
    case "upstream_error":
      // Also a rejected service account, so keep the server's words: they tell the two apart.
      return `Jira didn't answer as expected: ${e.message}`;
    case "not_implemented":
      return "Import isn't set up on this server.";
    case "forbidden":
      return "You don't have permission to import tickets.";
    case "unauthorized":
      return "Signed out · sign in again.";
    default:
      // validation_error (an unparseable ref, or a project outside the Space) and anything else:
      // the server's own message says what was wrong.
      return e.message;
  }
}
