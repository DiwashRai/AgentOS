// Section 0 probe: proves the generated types, CSRF client and stub API work end to end.
// The frontend session replaces this with the real shell, board and ticket view.
import { useState } from "react";

import type { Schemas } from "./api/client";
import { useLogin, useMe, useTickets } from "./api/queries";

const GROUPS: Schemas["TicketSummary"]["attention"][] = [
  "needs_you",
  "stuck",
  "moving",
  "waiting",
  "up_next",
  "done",
];

function Login() {
  const [username, setUsername] = useState("dev");
  const login = useLogin();
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        login.mutate(username);
      }}
    >
      <label>
        Username{" "}
        <input
          value={username}
          onChange={(e) => {
            setUsername(e.target.value);
          }}
        />
      </label>
      <button type="submit">Sign in (dev)</button>
      {login.error && <p role="alert">{login.error.message}</p>}
    </form>
  );
}

function Board() {
  const tickets = useTickets();
  if (tickets.isPending) return <p>Loading…</p>;
  if (tickets.isError) return <p role="alert">{tickets.error.message}</p>;
  return (
    <div style={{ display: "flex", gap: 16 }}>
      {GROUPS.map((group) => (
        <section key={group}>
          <h2>{group}</h2>
          <ul>
            {tickets.data
              .filter((t) => t.attention === group)
              .map((t) => (
                <li key={t.key}>
                  {t.key}: {t.title} [{t.phase_track.map((s) => s.state).join(" ")}] {t.now.text}
                </li>
              ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

export function App() {
  const me = useMe();
  if (me.isPending) return <p>Loading…</p>;
  if (me.isError) return <p role="alert">{me.error.message}</p>;
  return (
    <main>
      <h1>AgentOS</h1>
      {me.data ? <Board /> : <Login />}
    </main>
  );
}
