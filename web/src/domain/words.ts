// One vocabulary for every view (DESIGN §16). Never "Needs me", never "Stuck".
// Everything renders in sentence case as written (visual refresh: no mono caps anywhere but the
// YOU pill), so these strings are exactly what the screen shows.
import type { Schemas } from "../api/client";
import type { OnlineKey } from "./clients";
import type { DisplayKind } from "./outcome";
import type { Attention, StatusKind } from "./status";

/** The word for each server `attention` value: the one definition of the status words. */
export const attentionWords: Record<Attention, string> = {
  needs_you: "Needs you",
  waiting: "Waiting on others",
  running: "Running",
  queued: "Queued",
  blocked: "Blocked",
  inbox: "Inbox",
  done: "Done",
};

/** The word for each status kind (marks, legends, group heads): its attention's word. */
export const statusWords: Record<StatusKind, string> = {
  you: attentionWords.needs_you,
  wait: attentionWords.waiting,
  blk: attentionWords.blocked,
  run: attentionWords.running,
  queued: attentionWords.queued,
  done: attentionWords.done,
  idle: attentionWords.inbox,
};

/** How a done ticket ended (`resolution.outcome`); src/domain/outcome.ts picks it. */
export const outcomeWords: Record<Schemas["Resolution"]["outcome"], string> = {
  completed: "Complete",
  abandoned: "Abandoned",
};

/** The word for every mark a ticket can show (a glyph-only mark's accessible name): the status
 *  words, with Abandoned for a done ticket that was abandoned. */
export const displayWords: Record<DisplayKind, string> = {
  ...statusWords,
  abandoned: outcomeWords.abandoned,
};

/** Where an imported ticket came from (`source.source`), as a name: "Jira". */
export const sourceNames: Record<Schemas["SourceLink"]["source"], string> = {
  jira: "Jira",
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
  doneRecent: "Done recently",
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
  noRepo: "No repo",
} as const;
