// Sign in (not designed in the pack: built in the same quiet language). The form follows
// /api/config: dev auth → a username; Jira Cloud → email + API token; Jira Data Center → a PAT.
import { useState } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router";

import type { Schemas } from "../../api/client";
import { useConfig, useLogin, useMe } from "../../api/queries";
import s from "./LoginPage.module.css";
import { jiraHost, loginErrorText } from "./loginError";
import { Logo } from "./Logo";
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

const ATLASSIAN_TOKENS = "https://id.atlassian.com/manage-profile/security/api-tokens";

/** Where the token comes from, when the Space names its Jira. */
function Note({ mode, jiraUrl }: { mode: Mode; jiraUrl: string | null }) {
  if (mode === "dev" || !jiraUrl) return <p className={s.note}>{NOTES[mode]}</p>;
  const host = jiraHost(jiraUrl);
  if (mode === "cloud") {
    return (
      <p className={s.note}>
        Use the Atlassian email you sign in to {host} with, and an{" "}
        <a className={s.link} href={ATLASSIAN_TOKENS} target="_blank" rel="noreferrer">
          API token
        </a>{" "}
        for that account.
      </p>
    );
  }
  return (
    <p className={s.note}>
      Use a personal access token from your Jira profile at{" "}
      <a className={s.link} href={jiraUrl} target="_blank" rel="noreferrer">
        {host}
      </a>
      .
    </p>
  );
}

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

function LoginForm({ config, next }: { config: Schemas["AppConfig"]; next: string }) {
  const mode = modeOf(config);
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
        <Logo />
        <h1 className={s.title}>Sign in to {config.space_name || "AgentOS"}</h1>
      </div>
      <Note mode={mode} jiraUrl={config.jira_url} />
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
          {loginErrorText(login.error)}
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
        <LoginForm config={config.data} next={next} />
      )}
    </main>
  );
}
