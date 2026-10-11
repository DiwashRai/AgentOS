// Placeholder views (All teams). Settings lives in ./Settings.tsx.
import { Surface } from "../Surface";
import { TopBar } from "../TopBar";
import s from "./views.module.css";

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
