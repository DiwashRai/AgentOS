// Where Dispatch run, Approve (continue), Retry and Run from… send a run: one rule for every
// surface. The choice is offered only to the ticket's owner who has at least one client token, and
// defaults to "My client" while one of their clients is online, else to the server. Everyone else
// sends `server` and sees no choice. The server is the authority: a client dispatch by anyone but
// the owner is a 403 `forbidden`.
import type { Schemas } from "../../api/client";
import { onlineKey } from "../../domain/clients";
import { isMine, type MeRef } from "../../domain/status";
import { onlineWords, targetWords } from "../../domain/words";

export type DispatchTarget = Schemas["DispatchRequest"]["target"];

export interface TargetChoice {
  /** Show the Server / My client choice. */
  offer: boolean;
  /** What a dispatch sends unless the user picks the other one. */
  target: DispatchTarget;
  /** One of my clients is online. */
  online: boolean;
}

const SERVER_ONLY: TargetChoice = { offer: false, target: "server", online: false };

/** The choice for `t`, given me and my client tokens (undefined while they load). */
export function targetChoice(
  t: { owner: Pick<Schemas["Person"], "id"> | null },
  me: MeRef,
  clients: readonly Pick<Schemas["ClientToken"], "online">[] | undefined,
): TargetChoice {
  if (!isMine(t, me) || !clients || clients.length === 0) return SERVER_ONLY;

  const online = clients.some((c) => c.online);
  return { offer: true, target: online ? "client" : "server", online };
}

/** The target a dispatch sends: the user's pick while the choice is offered, else the default. */
export function chosenTarget(choice: TargetChoice, picked: DispatchTarget | null): DispatchTarget {
  return choice.offer && picked !== null ? picked : choice.target;
}

/** The picker's options, My client first; it says when none of my clients is online. */
export function targetOptions(choice: TargetChoice): { value: DispatchTarget; label: string }[] {
  const client = choice.online
    ? targetWords.client
    : `${targetWords.client} · ${onlineWords[onlineKey(false)].toLowerCase()}`;
  return [
    { value: "client", label: client },
    { value: "server", label: targetWords.server },
  ];
}

export function isTarget(v: string): v is DispatchTarget {
  return v === "server" || v === "client";
}
