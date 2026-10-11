// One vocabulary for every view (DESIGN §16). Never "Needs me", never "Stuck".
// Mono caps labels are uppercased by CSS; these stay in sentence case.
import type { Attention, StatusKind } from "./status";

/** The word for each status kind (marks, legends, group heads). */
export const statusWords: Record<StatusKind, string> = {
  you: "Needs you",
  wait: "Waiting on others",
  blk: "Blocked",
  run: "Running",
  queued: "Queued",
  done: "Merged",
  idle: "Inbox",
};

/** The word for each server `attention` value (web/AGENTS.md mapping table). `moving` covers both
 *  Running and Queued; use `statusWords[statusKind(t)]` when the split matters. */
export const attentionWords: Record<Attention, string> = {
  needs_you: "Needs you",
  waiting: "Waiting on others",
  stuck: "Blocked",
  moving: "Agents at work",
  up_next: "Inbox",
  done: "Merged",
};

/** Other fixed phrases from DESIGN §16. */
export const words = {
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
  requestChanges: "Request changes",
  agentReads: "The agent reads",
  addContext: "+ Add context",
  newTicket: "New ticket",
  pinned: "New · pinned",
} as const;
