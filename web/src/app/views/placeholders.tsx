// Placeholder views: each renders its final top bar (DESIGN §6–8) over an empty surface.
// The real views replace these one by one.
import { useNavigate, useParams } from "react-router";

import { useLogout } from "../../api/queries";
import { CloseIcon } from "../icons";
import { Quiet } from "../Quiet";
import { Surface } from "../Surface";
import { TopBar } from "../TopBar";
import s from "./views.module.css";

export function CompactView() {
  return (
    <>
      <TopBar title="Compact" context="Every ticket" search="Search or jump to a ticket" />
      <Surface className={s.fill} />
    </>
  );
}

export function TeamsView() {
  return (
    <>
      <TopBar
        title="All teams"
        context="Every repo by stage"
        counters={["running", "queued", "|", "needYou"]}
        search="Jump to a repo or ticket"
      />
      <Surface className={s.fill} />
    </>
  );
}

export function TicketView() {
  const { key = "", tab = "overview" } = useParams();
  const navigate = useNavigate();
  return (
    <>
      <TopBar
        title="Ticket"
        middle={<span className={s.crumb}>{key}</span>}
        search="Search or jump to a ticket"
        action={
          <button
            type="button"
            className={s.iconButton}
            aria-label="Close ticket"
            title="Close ticket"
            onClick={() => {
              void navigate("/");
            }}
          >
            <CloseIcon />
          </button>
        }
      />
      <Surface className={s.fill}>
        <Quiet>{`${key} · ${tab}`}</Quiet>
      </Surface>
    </>
  );
}

export function TemplateView() {
  const logout = useLogout();
  return (
    <>
      <TopBar title="Template" context="The pipeline every new ticket starts from" />
      <Surface className={s.fill}>
        <button
          type="button"
          className={s.quietButton}
          disabled={logout.isPending}
          onClick={() => {
            logout.mutate();
          }}
        >
          Sign out
        </button>
        {logout.error && <Quiet error>{logout.error.message}</Quiet>}
      </Surface>
    </>
  );
}
