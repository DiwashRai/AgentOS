// A personal client (`worker.py --client`) and the runs it serves, for display. `online` comes from
// the server (the client polled within the last minute); it describes a machine, not a person, and
// shows as a word, never a dot: in Settings › My client, the dispatch choice and a queued client run.
import type { Schemas } from "../api/client";

export type OnlineKey = "online" | "offline";

export function onlineKey(online: boolean): OnlineKey {
  return online ? "online" : "offline";
}

/** What a client run not started yet waits for: the server's line ("waiting for Sam's client",
 *  "starting on Sam's client") and whether that client is online. */
export interface ClientWait {
  text: string;
  online: OnlineKey;
}

/** The wait of a ticket whose latest run is a client run not started yet (queued, or handed to
 *  the client and not claimed); null for any other run, which waits for the server's runner. */
export function clientWait(
  t: Pick<Schemas["TicketSummary"], "now" | "latest_run">,
): ClientWait | null {
  const run = t.latest_run;
  if (run?.target !== "client") return null;
  if (run.status !== "queued" && run.status !== "dispatched") return null;
  return { text: t.now.text, online: onlineKey(run.client_online === true) };
}
