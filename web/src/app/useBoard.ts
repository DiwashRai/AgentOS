// One hook every view reads the board through: the tickets, me, and the shared counts and groups
// from src/domain. Views never count or group for themselves.
import { useMemo } from "react";

import type { Schemas } from "../api/client";
import { useMe, useTickets } from "../api/queries";
import { counts } from "../domain/counts";
import { groups } from "../domain/groups";

const NO_TICKETS: readonly Schemas["TicketSummary"][] = [];

export function useBoard() {
  const meQuery = useMe();
  const ticketsQuery = useTickets();
  const me = meQuery.data ?? null;
  const tickets = ticketsQuery.data ?? NO_TICKETS;
  const c = useMemo(() => counts(tickets, me), [tickets, me]);
  const g = useMemo(() => groups(tickets, me), [tickets, me]);
  return {
    me,
    tickets,
    counts: c,
    groups: g,
    isPending: ticketsQuery.isPending,
    error: ticketsQuery.error,
  };
}
