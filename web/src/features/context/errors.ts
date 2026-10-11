// Map a failed bundle write to what the editor says (SPEC §6: versioned files, 409s). Branches on
// the typed ApiError code, never on the message.
import { ApiError } from "../../api/client";

export type WriteOp = "save" | "create" | "delete";

export interface WriteProblem {
  text: string;
  /** Offer Reload: the server has a newer version than the one the edit started from. */
  reload: boolean;
}

export const STALE_TEXT =
  "Changed since you opened it: reload (your edits will be lost) or copy them first.";
export const LOCKED_TEXT = "A run is working on these files. Editing unlocks when it finishes.";

export function writeProblem(err: unknown, op: WriteOp): WriteProblem {
  if (!(err instanceof ApiError)) {
    return { text: err instanceof Error ? err.message : "Something went wrong.", reload: false };
  }
  switch (err.code) {
    case "stale_version":
      return op === "create"
        ? { text: "A file with this path already exists.", reload: false }
        : { text: STALE_TEXT, reload: true };
    case "bundle_locked":
    case "run_active":
      return { text: LOCKED_TEXT, reload: false };
    case "file_not_found":
      return { text: "This file was deleted by someone else.", reload: true };
    case "forbidden":
      return { text: "You don't have write access to this ticket.", reload: false };
    default:
      // 422 validation_error / 413 too_large carry a useful server message ("path: …").
      return { text: err.message, reload: false };
  }
}
