// Placeholder views (All teams) and the Settings › Template page wrapper.
import { useLogout } from "../../api/queries";
import { TemplatePage } from "../../features/pipeline";
import { Quiet } from "../Quiet";
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

/** Settings: the pipeline template. Sign out lives here until the avatar gets a menu. */
export function TemplateView() {
  const logout = useLogout();
  return (
    <>
      <TopBar
        title="Template"
        context="The pipeline every new ticket starts from"
        search={false}
        action={
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
        }
      />
      <Surface className={s.scroll}>
        {logout.error && <Quiet error>{logout.error.message}</Quiet>}
        <TemplatePage />
      </Surface>
    </>
  );
}
