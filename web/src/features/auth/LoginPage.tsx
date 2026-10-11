// Sign in (not designed in the pack: built in the same quiet language). The form follows
// /api/config: dev auth → a username; Jira Cloud → email + API token; Jira Data Center → a PAT.
import { useState } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router";

import type { Schemas } from "../../api/client";
import { useConfig, useLogin, useMe } from "../../api/queries";
import s from "./LoginPage.module.css";
import { safeNext } from "./next";

type Mode = "dev" | "cloud" | "datacenter";

function modeOf(config: Schemas["AppConfig"]): Mode {
  if (config.auth_mode === "dev") return "dev";
  return config.jira_deployment === "datacenter" ? "datacenter" : "cloud";
}

const NOTES: Record<Mode, string> = {
  dev: "Dev sign-in. Any username works on this machine.",
  cloud: "Use your Atlassian email and an API token.",
  datacenter: "Use a Jira personal access token.",
};

function Field(props: {
  label: string;
  type?: string;
  autoComplete: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <label className={s.field}>
      <span className={s.label}>{props.label}</span>
      <input
        className={s.input}
        type={props.type ?? "text"}
        autoComplete={props.autoComplete}
        required
        value={props.value}
        onChange={(e) => {
          props.onChange(e.target.value);
        }}
      />
    </label>
  );
}

function LoginForm({ mode, next }: { mode: Mode; next: string }) {
  const navigate = useNavigate();
  const login = useLogin();
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [token, setToken] = useState("");

  const submit = () => {
    const body: Schemas["LoginRequest"] =
      mode === "dev" ? { username } : mode === "cloud" ? { email, token } : { token };
    login.mutate(body, {
      onSuccess: () => {
        void navigate(next, { replace: true });
      },
    });
  };

  return (
    <form
      className={s.sheet}
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <div className={s.head}>
        <span className={s.logo} aria-hidden="true">
          <svg width="14" height="14" viewBox="0 0 14 14">
            <circle cx="7" cy="7" r="2.4" style={{ fill: "var(--accent-mark)" }} />
            <circle
              cx="7"
              cy="7"
              r="5.6"
              fill="none"
              style={{ stroke: "var(--bubble-mine-ink)" }}
              strokeWidth="1.3"
            />
          </svg>
        </span>
        <h1 className={s.title}>Sign in to AgentOS</h1>
      </div>
      <p className={s.note}>{NOTES[mode]}</p>
      {mode === "dev" && (
        <Field label="Username" autoComplete="username" value={username} onChange={setUsername} />
      )}
      {mode === "cloud" && (
        <Field
          label="Email"
          type="email"
          autoComplete="username"
          value={email}
          onChange={setEmail}
        />
      )}
      {mode !== "dev" && (
        <Field
          label={mode === "cloud" ? "API token" : "Personal access token"}
          type="password"
          autoComplete="current-password"
          value={token}
          onChange={setToken}
        />
      )}
      {login.error && (
        <p className={s.error} role="alert">
          {login.error.message}
        </p>
      )}
      <button type="submit" className={s.submit} disabled={login.isPending}>
        {login.isPending ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}

export function LoginPage() {
  const [params] = useSearchParams();
  const next = safeNext(params.get("next"));
  const me = useMe();
  const config = useConfig();

  if (me.data) return <Navigate to={next} replace />;

  return (
    <main className={s.page}>
      {config.isPending || me.isPending ? (
        <p className={s.quiet} role="status">
          Loading
        </p>
      ) : config.isError ? (
        <p className={s.quiet} role="alert">
          {`Can't reach the server · ${config.error.message}`}
        </p>
      ) : (
        <LoginForm mode={modeOf(config.data)} next={next} />
      )}
    </main>
  );
}
