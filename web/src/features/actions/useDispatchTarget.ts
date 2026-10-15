// The dispatch target for one ticket (features/actions/target.ts) plus the user's pick, which
// lasts while the surface is mounted. The Ticket page holds one for its header and its Pipeline
// tab's Run from…, so both show the same pick; a row's useTicketAction holds its own.
import { useState } from "react";

import { useClients, useMe } from "../../api/queries";
import { chosenTarget, type DispatchTarget, type TargetChoice, targetChoice } from "./target";

export interface DispatchTargetState extends TargetChoice {
  /** What the next dispatch sends. */
  value: DispatchTarget;
  pick: (target: DispatchTarget) => void;
}

export function useDispatchTarget(t: { owner: { id: number } | null }): DispatchTargetState {
  const me = useMe();
  const clients = useClients();
  const [picked, setPicked] = useState<DispatchTarget | null>(null);

  const choice = targetChoice(t, me.data ?? null, clients.data);
  return { ...choice, value: chosenTarget(choice, picked), pick: setPicked };
}
