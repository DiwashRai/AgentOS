// Settings › My client: the signed-in user's client tokens. Writers create one with a name and see
// it once, with the lines that start `worker.py --client` in PowerShell and in sh; everyone
// approved lists their own tokens (state and last seen, as words) and revokes them. Renders a
// content column; the route wraps it in the shell's TopBar + Surface.
import { useEffect, useState } from "react";

import { useClients, useCreateClient, useMe, useRevokeClient } from "../../../api/queries";
import { Button } from "../../../ui";
import { Field, fieldStyles as f } from "../../pipeline/fields";
import pages from "../../pipeline/pages.module.css";
import { useNow } from "../../runs/useNow";
import { shortDate } from "../../users/model";
import s from "./Clients.module.css";
import {
  canCreate,
  clientErrorText,
  type ClientToken,
  type ClientTokenCreated,
  NAME_ISSUE_TEXT,
  NAME_MAX,
  nameIssue,
  seenText,
  setupLines,
  SHELLS,
  STATE_TEXT,
  stateKey,
  summary,
} from "./model";

const COLUMNS = 5;
/** How long a Copy button says "Copied" before it reads "Copy" again. */
const COPIED_MS = 2_000;

function Line({ children, error }: { children: string; error?: boolean }) {
  return (
    <p
      className={pages.quiet}
      role={error ? "alert" : "status"}
      data-error={error ? "" : undefined}
    >
      {children}
    </p>
  );
}

/** Copies `text`; says so for a moment. The clipboard API needs a secure origin, so a failure asks
 *  for a manual copy instead. */
function CopyButton({ text, label }: { text: string; label: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");

  useEffect(() => {
    if (state !== "copied") return;
    const timer = setTimeout(() => {
      setState("idle");
    }, COPIED_MS);
    return () => {
      clearTimeout(timer);
    };
  }, [state]);

  return (
    <Button
      size="sm"
      variant="quiet"
      aria-label={label}
      onClick={() => {
        // Through a promise, so a missing clipboard (an insecure origin) lands in the failure.
        Promise.resolve()
          .then(() => navigator.clipboard.writeText(text))
          .then(
            () => {
              setState("copied");
            },
            () => {
              setState("failed");
            },
          );
      }}
    >
      {state === "copied" ? "Copied" : state === "failed" ? "Select and copy" : "Copy"}
    </Button>
  );
}

/** The new token, shown this once, with the lines to start its client. */
function ShownOnce({ created, onDone }: { created: ClientTokenCreated; onDone: () => void }) {
  return (
    <section className={s.once} aria-label={`New token for ${created.client.name}`}>
      <div className={s.onceHead}>
        <h3 className={s.onceTitle}>{`Token for ${created.client.name}`}</h3>
        <span className={s.onceNote}>Copy it now: it is shown only this once.</span>
        <span className={pages.spacer} />
        <Button size="sm" onClick={onDone}>
          Done
        </Button>
      </div>
      {SHELLS.map((sh) => {
        const text = setupLines(sh.id, created).join("\n");
        return (
          <div key={sh.id} className={s.shell}>
            <div className={s.shellHead}>
              <span className={pages.k}>{sh.label}</span>
              <CopyButton text={text} label={`Copy the ${sh.label} lines`} />
            </div>
            <pre className={s.code}>{text}</pre>
          </div>
        );
      })}
      <p className={s.hint}>
        Run it beside worker.py from the AgentOS repo (backend/worker/), the same version as this
        server. Agent and git credentials go in a file passed with --env-file.
      </p>
    </section>
  );
}

function CreateForm({ onCreated }: { onCreated: (c: ClientTokenCreated) => void }) {
  const create = useCreateClient();
  const [name, setName] = useState("");
  const [touched, setTouched] = useState(false);
  const issue = nameIssue(name);
  const shown = touched && issue !== null ? NAME_ISSUE_TEXT[issue] : undefined;

  return (
    <form
      className={s.create}
      onSubmit={(e) => {
        e.preventDefault();
        setTouched(true);
        if (issue !== null) return;
        create.mutate(
          { name: name.trim() },
          {
            onSuccess: (c) => {
              setName("");
              setTouched(false);
              onCreated(c);
              // The token lives only in the page's state, never in the mutation cache.
              create.reset();
            },
          },
        );
      }}
    >
      <Field
        label="New token"
        className={s.name}
        error={shown ?? (create.error ? clientErrorText(create.error) : undefined)}
        hint="Name the machine it runs on."
      >
        {({ id, describedBy, invalid }) => (
          <input
            id={id}
            className={f.input}
            value={name}
            // Headroom over NAME_MAX for surrounding spaces the server strips; nameIssue checks the rest.
            maxLength={NAME_MAX + 20}
            placeholder="work laptop"
            readOnly={create.isPending}
            aria-describedby={describedBy}
            aria-invalid={invalid}
            onChange={(e) => {
              setName(e.target.value);
              create.reset();
            }}
          />
        )}
      </Field>
      <Button type="submit" variant="ink" disabled={create.isPending}>
        {create.isPending ? "Creating…" : "Create token"}
      </Button>
    </form>
  );
}

/** One token, with Revoke (asks first, inline) and its own mutation's pending and error. */
function ClientRow({ client, now }: { client: ClientToken; now: number }) {
  const revoke = useRevokeClient();
  const [confirming, setConfirming] = useState(false);
  const state = stateKey(client);

  return (
    <tbody className={s.client} aria-busy={revoke.isPending}>
      <tr>
        <th scope="row" className={s.clientName}>
          {client.name}
        </th>
        <td className={s.state} data-state={state}>
          {STATE_TEXT[state]}
        </td>
        <td className={s.time}>{seenText(client.last_seen_at, now)}</td>
        <td className={s.time}>{shortDate(client.created_at)}</td>
        <td className={s.end}>
          <Button
            size="sm"
            variant="quiet"
            aria-label={`Revoke ${client.name}`}
            aria-expanded={confirming}
            disabled={revoke.isPending || confirming}
            onClick={() => {
              revoke.reset();
              setConfirming(true);
            }}
          >
            Revoke
          </Button>
        </td>
      </tr>
      {confirming ? (
        <tr className={s.note}>
          <td colSpan={COLUMNS}>
            <span className={s.confirm} role="group" aria-label={`Revoke ${client.name}`}>
              <span>{`Revoke ${client.name}? Its client stops at its next poll.`}</span>
              <Button
                size="sm"
                variant="danger"
                onClick={() => {
                  setConfirming(false);
                  revoke.mutate(client.id);
                }}
              >
                Revoke
              </Button>
              <Button
                size="sm"
                variant="quiet"
                onClick={() => {
                  setConfirming(false);
                }}
              >
                Cancel
              </Button>
            </span>
          </td>
        </tr>
      ) : null}
      {revoke.error ? (
        <tr className={s.note}>
          <td colSpan={COLUMNS}>
            <span className={s.error} role="alert">
              {clientErrorText(revoke.error)}
            </span>
          </td>
        </tr>
      ) : null}
    </tbody>
  );
}

export function ClientsPage() {
  const me = useMe();
  const clients = useClients();
  const now = useNow(30_000);
  const [created, setCreated] = useState<ClientTokenCreated | null>(null);

  if (me.isPending || clients.isPending) return <Line>Loading clients</Line>;
  // A failed refetch keeps the last list (and a token shown once); only a first load fails here.
  if (clients.data === undefined) {
    return <Line error>{`Can't load clients · ${clients.error.message}`}</Line>;
  }

  const list = clients.data;
  const writer = canCreate(me.data?.role);

  return (
    <div className={pages.page}>
      <header className={pages.bar}>
        <h2 className={pages.title}>My client</h2>
        <span className={pages.meta}>{summary(list)}</span>
      </header>

      <p className={pages.banner} role="note">
        <span className={pages.k}>Your machine</span>A client runs the tickets you own on your own
        machine, with your own agent login and git credentials. Dispatch a ticket you own to My
        client and it waits until your client picks it up.
      </p>

      {writer ? (
        <CreateForm
          onCreated={(c) => {
            setCreated(c);
          }}
        />
      ) : (
        <Line>Only writers can create client tokens.</Line>
      )}

      {created ? (
        <ShownOnce
          created={created}
          onDone={() => {
            setCreated(null);
          }}
        />
      ) : null}

      {clients.isError ? (
        <Line error>{`Can't refresh clients · ${clients.error.message}`}</Line>
      ) : null}

      {list.length === 0 ? (
        <Line>No client tokens yet.</Line>
      ) : (
        <div className={s.frame}>
          <table className={s.table}>
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">State</th>
                <th scope="col">Last seen</th>
                <th scope="col">Created</th>
                <th scope="col">
                  <span className={s.hidden}>Actions</span>
                </th>
              </tr>
            </thead>
            {list.map((c) => (
              <ClientRow key={c.id} client={c} now={now} />
            ))}
          </table>
        </div>
      )}
    </div>
  );
}
