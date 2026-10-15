// Settings › My client's pure core. A client token lets `worker.py --client` on the user's own
// machine take the runs they dispatch to "My client" (tickets they own), with their own agent login
// and git credentials. Here: the lines shown once with a new token, the name rule, which last-seen
// and state wording a token gets, and the error lines. The server stays the authority (it strips
// and checks the name, and owns `online`).
import { ApiError, type Schemas } from "../../../api/client";
import { onlineKey, type OnlineKey } from "../../../domain/clients";
import { age, type Instant } from "../../../domain/format";
import { onlineWords } from "../../../domain/words";

export type ClientToken = Schemas["ClientToken"];
export type ClientTokenCreated = Schemas["ClientTokenCreated"];
type Role = Schemas["Me"]["role"];

/** The environment variable the client reads its token from (never a command-line argument). */
export const TOKEN_ENV = "AGENTOS_CLIENT_TOKEN";

/** The server's limit on a token's name (after stripping). */
export const NAME_MAX = 80;

export type Shell = "pwsh" | "sh";
export const SHELLS: readonly { id: Shell; label: string }[] = [
  { id: "pwsh", label: "PowerShell" },
  { id: "sh", label: "sh" },
];

/** Characters a shell word may hold without quotes in sh and in pwsh's argument mode (a URL's
 *  usual ones; `,` and `@` are special in pwsh, so they get quotes). */
const BARE = /^[\w%+=:./-]+$/;

/** `s` as one shell word: bare when it is safe, else single-quoted for that shell. */
export function shellWord(shell: Shell, s: string): string {
  if (BARE.test(s)) return s;
  return shell === "pwsh" ? `'${s.replaceAll("'", "''")}'` : `'${s.replaceAll("'", `'\\''`)}'`;
}

/** The command that starts a client against `serverUrl`. The token is never on it. */
export function clientCommand(shell: Shell, serverUrl: string): string {
  return `python worker.py --client --server ${shellWord(shell, serverUrl)}`;
}

/** What to paste into `shell` after creating a token: put it in the environment, then start the
 *  client. pwsh's `$env:` assignment always quotes its value. */
export function setupLines(
  shell: Shell,
  created: Pick<ClientTokenCreated, "token" | "server_url">,
): string[] {
  const set =
    shell === "pwsh"
      ? `$env:${TOKEN_ENV} = '${created.token.replaceAll("'", "''")}'`
      : `export ${TOKEN_ENV}=${shellWord("sh", created.token)}`;
  return [set, clientCommand(shell, created.server_url)];
}

/** Why a name can't be sent yet, as a key: blank, or longer than NAME_MAX once stripped. */
export type NameIssue = "empty" | "long";

export function nameIssue(name: string): NameIssue | null {
  const n = name.trim().length;
  if (n === 0) return "empty";
  return n > NAME_MAX ? "long" : null;
}

export const NAME_ISSUE_TEXT: Record<NameIssue, string> = {
  empty: "Name the machine, e.g. work laptop",
  long: `At most ${String(NAME_MAX)} characters`,
};

/** Creating needs writer (dispatch does); listing and revoking your own needs only approval. */
export function canCreate(role: Role | undefined): boolean {
  return role === "writer" || role === "admin";
}

/** When a token's client last polled, as a key: never, within the minute, or an age. */
export type SeenKey = "never" | "now" | "ago";

export function seenKey(at: string | null, now: Instant): SeenKey {
  if (at === null) return "never";
  return age(at, now) === "now" ? "now" : "ago";
}

export function seenText(at: string | null, now: Instant): string {
  switch (seenKey(at, now)) {
    case "never":
      return "Never";
    case "now":
      return "Just now";
    case "ago":
      return `${age(at ?? now, now)} ago`;
  }
}

/** A token's state as a key: the server's `online`, else offline, or never connected. */
export type StateKey = OnlineKey | "never";

export function stateKey(c: Pick<ClientToken, "online" | "last_seen_at">): StateKey {
  if (c.online) return "online";
  return c.last_seen_at === null ? "never" : onlineKey(false);
}

export const STATE_TEXT: Record<StateKey, string> = {
  ...onlineWords,
  never: "Never connected",
};

/** `2 clients · 1 online` */
export function summary(list: readonly Pick<ClientToken, "online">[]): string {
  const n = list.length;
  const online = list.filter((c) => c.online).length;
  return `${n === 1 ? "1 client" : `${String(n)} clients`} · ${String(online)} online`;
}

/** One line for a failed create or revoke. */
export function clientErrorText(e: unknown): string {
  if (!(e instanceof ApiError)) return e instanceof Error ? e.message : "Something went wrong";
  switch (e.code) {
    case "forbidden":
      return "Only writers can create client tokens";
    case "not_found":
      return "That token is already gone";
    default:
      return e.message;
  }
}
