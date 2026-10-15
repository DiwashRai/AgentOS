// The Ticket page (DESIGN §7, layout A, with our cuts: no queue overlay, no Activity tab, no
// J/K/Q keys). The ticket column (header, strip slot, tabs) scrolls on its own; Talk is full height.
// Route: /t/:key/:tab? with tab = overview (default) | runs | context | pipeline.
import { Link as RouterLink, useLocation, useNavigate, useParams } from "react-router";

import { ApiError } from "../../api/client";
import { useFiles, useRuns, useTicket } from "../../api/queries";
import { TopBar } from "../../app/TopBar";
import { CloseIcon, IconButton, StatusLine, Tabs, linkClass, type TabItem } from "../../ui";
import { useDispatchTarget } from "../actions/useDispatchTarget";
import { ContextTab } from "../context/ContextTab";
import { PipelineTab } from "../pipeline";
import { RunsTab } from "../runs/RunsTab";
import { Talk } from "../talk/Talk";
import { Header } from "./Header";
import { parseTab, ticketPath, type TicketTab } from "./model";
import { Overview } from "./Overview";
import { Strip } from "./Strip";
import s from "./TicketPage.module.css";

function TicketBody({ ticketKey, tab }: { ticketKey: string; tab: TicketTab }) {
  const navigate = useNavigate();
  const { search } = useLocation();
  const ticket = useTicket(ticketKey);
  const files = useFiles(ticketKey);
  const runs = useRuns(ticketKey);
  // One dispatch target pick for the page: the header's actions and the Pipeline tab's Run from….
  const target = useDispatchTarget({ owner: ticket.data?.owner ?? null });

  if (ticket.isPending) return <StatusLine inset>{`Loading ${ticketKey}`}</StatusLine>;
  if (ticket.isError) {
    const e = ticket.error;
    if (e instanceof ApiError && (e.code === "ticket_not_found" || e.status === 404)) {
      return (
        <div className={s.missing}>
          <StatusLine inset>{`Ticket not found · ${ticketKey}`}</StatusLine>
          <RouterLink className={linkClass} to="/">
            Back to My queue
          </RouterLink>
        </div>
      );
    }
    return <StatusLine inset error>{`Can't load ${ticketKey} · ${e.message}`}</StatusLine>;
  }

  const t = ticket.data;
  const fileList = files.data ?? [];
  const items: TabItem<TicketTab>[] = [
    { id: "overview", label: "Overview" },
    { id: "runs", label: "Runs", count: runs.data?.length },
    { id: "context", label: "Context", count: files.data?.length },
    { id: "pipeline", label: "Pipeline" },
  ];
  const wide = tab !== "overview";

  return (
    <div className={s.page}>
      <div className={s.col}>
        <Header t={t} target={target} />
        <Strip t={t} files={fileList} />
        <div className={s.doc}>
          <Tabs
            items={items}
            value={tab}
            label="Ticket sections"
            idPrefix="ticket"
            onChange={(id) => {
              void navigate(ticketPath(t.key, id, search));
            }}
          />
          <div
            className={wide ? s.wide : undefined}
            role="tabpanel"
            id={`ticket-panel-${tab}`}
            aria-labelledby={`ticket-tab-${tab}`}
          >
            {tab === "overview" ? <Overview t={t} files={fileList} /> : null}
            {tab === "runs" ? <RunsTab ticketKey={t.key} /> : null}
            {tab === "context" ? (
              <ContextTab ticketKey={t.key} runActive={t.active_run_id !== null} />
            ) : null}
            {tab === "pipeline" ? <PipelineTab ticketKey={t.key} target={target} /> : null}
          </div>
        </div>
      </div>
      <div className={s.talk}>
        <Talk ticketKey={t.key} variant="ticket" />
      </div>
    </div>
  );
}

export function TicketPage() {
  const { key = "", tab } = useParams();
  const navigate = useNavigate();
  return (
    <>
      <TopBar
        title="Ticket"
        middle={<span className={s.crumb}>{key}</span>}
        search="Search or jump to a ticket"
        action={
          <IconButton
            label="Back to My queue"
            onClick={() => {
              void navigate("/");
            }}
          >
            <CloseIcon />
          </IconButton>
        }
      />
      <TicketBody key={key} ticketKey={key} tab={parseTab(tab)} />
    </>
  );
}
