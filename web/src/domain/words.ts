// One vocabulary for every view (DESIGN §16). Never "Needs me", never "Stuck".
// Everything renders in sentence case as written (visual refresh: no mono caps anywhere but the
// YOU pill), so these strings are exactly what the screen shows.
import type { Schemas } from "../api/client";
import type { OnlineKey } from "./clients";
import type { Attention, StatusKind } from "./status";

/** The word for each status kind (marks, legends, group heads). */
export const statusWords: Record<StatusKind, string> = {
  you: "Needs you",
  wait: "Waiting on others",
  blk: "Blocked",
  run: "Running",
  queued: "Queued",
  done: "Done",
  idle: "Inbox",
};

/** How a done ticket ended (`resolution.outcome`); src/domain/outcome.ts picks it. */
export const outcomeWords: Record<Schemas["Resolution"]["outcome"], string> = {
  completed: "Complete",
  abandoned: "Abandoned",
};

/** The word for each server `attention` value (web/AGENTS.md mapping table). */
export const attentionWords: Record<Attention, string> = {
  needs_you: "Needs you",
  waiting: "Waiting on others",
  running: "Running",
  queued: "Queued",
  blocked: "Blocked",
  inbox: "Inbox",
  done: "Done",
};

/** A personal client's state (a machine, never a person; src/domain/clients.ts). */
export const onlineWords: Record<OnlineKey, string> = {
  online: "Online",
  offline: "Offline",
};

/** Where a dispatched run goes: the server's launcher, or the owner's own client. */
export const targetWords: Record<Schemas["DispatchRequest"]["target"], string> = {
  server: "Server",
  client: "My client",
};

/** Other fixed phrases from DESIGN §16. */
export const words = {
  readyToStart: "Ready to start",
  atWork: "Agents at work",
  waitingSomeone: "Waiting on someone",
  doneRecent: "Done recently",
  mergedToday: "Merged today",
  inboxNote: "Nobody on it yet",
  talk: "Talk",
  talkNote: "Just people here. Agents don't read this.",
  peek: "Peek",
  openTicket: "Open ticket →",
  dispatch: "Dispatch run",
  approve: "Approve",
  complete: "Complete",
  abandon: "Abandon",
  resolveNote: "Add a note (optional)",
  retry: "Retry",
  cancel: "Cancel",
  cancelling: "Cancelling…",
  reopen: "Reopen",
  open: "Open",
  requestChanges: "Request changes",
  agentReads: "The agent reads",
  addContext: "+ Add context",
  newTicket: "New ticket",
  pinned: "New · pinned",
} as const;
