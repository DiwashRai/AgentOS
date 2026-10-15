// The Peek host (DESIGN §9): mounted once in the shell, it shows the panel as a side-sheet dialog
// while `?peek=KEY` is set. Esc, the scrim and the "Close peek" button close it (removing `peek`,
// keeping the other params) after the 260 ms slide out; `Dialog` owns focus, the Tab trap and
// focus return.
import { Dialog } from "../../ui";
import s from "./Peek.module.css";
import { PeekPanel } from "./PeekPanel";
import { usePeek } from "./usePeek";

/** The panel's exit (fade + 24px slide); `Dialog` makes it 0 under reduced motion. */
const EXIT_MS = 260;

export function PeekHost() {
  const peek = usePeek();
  if (peek.key === null) return null;

  const ticketKey = peek.key;
  return (
    <Dialog
      label="Ticket peek"
      scrimLabel="Close peek"
      exitMs={EXIT_MS}
      className={s.peek}
      layerClassName={s.layer}
      onClose={peek.close}
    >
      {(close) => <PeekPanel ticketKey={ticketKey} onClose={close} />}
    </Dialog>
  );
}
