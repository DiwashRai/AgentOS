// Friendly text for a failed sign-in, keyed by the server's ErrorCode. Anything else shows the
// server's own message.
import { ApiError, type ErrorCode } from "../../api/client";

const TEXT: Partial<Record<ErrorCode, string>> = {
  invalid_credentials: "Jira didn't accept those credentials. Check the token and try again.",
  no_project_access: "Your Jira account can't see any of this Space's projects.",
  user_disabled: "This account is disabled. Ask an admin to re-enable it.",
  rate_limited: "Too many sign-in attempts. Wait a minute and try again.",
  missing_credentials: "Fill in every field to sign in.",
  upstream_error: "Jira didn't answer. Try again in a moment.",
};

export function loginErrorText(error: Error): string {
  if (error instanceof ApiError) return TEXT[error.code] ?? error.message;
  return error.message;
}

/** The host of the Space's Jira URL, for display ("acme.atlassian.net"); the raw value if unparsable. */
export function jiraHost(url: string): string {
  try {
    return new URL(url).host || url;
  } catch {
    return url;
  }
}
