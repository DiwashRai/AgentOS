// My queue (home), DESIGN §5. Placeholder: the top bar is final; the surface lists the personal
// groups as plain titles to prove the data flows (the real zones come later).
import { Link } from "react-router";

import type { Schemas } from "../../api/client";
import type { CountKey } from "../../domain/counts";
import { srcLabel } from "../../domain/format";
import type { Groups } from "../../domain/groups";
import { statusWords, words } from "../../domain/words";
import { Quiet } from "../Quiet";
import { Surface } from "../Surface";
import { TopBar } from "../TopBar";
import { useBoard } from "../useBoard";
import s from "./views.module.css";

type Ticket = Schemas["TicketSummary"];

const SECTIONS: { group: keyof Groups<Ticket>; title: string; count: CountKey }[] = [
  { group: "needYou", title: statusWords.you, count: "needYou" },
  { group: "atWork", title: words.atWork, count: "mine" },
  { group: "waitingMine", title: statusWords.wait, count: "waitingMine" },
  { group: "blocked", title: statusWords.blk, count: "blocked" },
  { group: "doneRecent", title: words.doneRecent, count: "done" },
];

export function QueueView() {
  const board = useBoard();
  const firstName = board.me?.display_name.split(/\s+/)[0];
  return (
    <>
      <TopBar
        title="My queue"
        context={firstName}
        counters={["needYou", "blockedMine", "mine", "|", "running", "queued"]}
      />
      <Surface className={s.queue}>
        {board.isPending && <Quiet>Loading tickets</Quiet>}
        {board.error && <Quiet error>{`Couldn't load tickets · ${board.error.message}`}</Quiet>}
        {SECTIONS.map(({ group, title, count }) => (
          <section key={group} aria-label={title}>
            <h2 className={s.groupHead}>
              {title} <span>{board.counts[count]}</span>
            </h2>
            <ul className={s.list}>
              {board.groups[group].map((t) => (
                <li key={t.key}>
                  <Link to={`/t/${t.key}`}>
                    <span className={s.ref}>{srcLabel(t)}</span>
                    <span className={s.title}>{t.title}</span>
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </Surface>
    </>
  );
}
