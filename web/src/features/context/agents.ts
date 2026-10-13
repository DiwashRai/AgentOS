// "Add an agent" on the Context tab: a stage's Agent field becomes `claude --agent <name>`, and
// Claude Code reads `.claude/agents/<name>.md` from the workspace. The worker writes every bundle
// file into the workspace and keeps it out of commits, so the bundle is the quickest place to ship
// one. Pure: the name rule, the path, the starter file, and the exists check.

/** Lowercase letters, digits and hyphens; starts with a letter or digit; at most 64 characters. */
export const AGENT_NAME_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;

export const AGENT_NAME_RULE =
  "Lowercase letters, digits and hyphens, up to 64 characters. Starts with a letter or digit.";

const AGENTS_DIR = ".claude/agents/";

/** What's wrong with a typed name, or null when it's usable. Empty asks for one. */
export function agentNameProblem(name: string): string | null {
  if (name === "") return "Give the agent a name, like reviewer.";
  return AGENT_NAME_RE.test(name) ? null : AGENT_NAME_RULE;
}

/** `reviewer` → `.claude/agents/reviewer.md`. */
export function agentPath(name: string): string {
  return `${AGENTS_DIR}${name}.md`;
}

/** The agent a bundle path defines (`.claude/agents/reviewer.md` → `reviewer`), else null. */
export function agentNameFromPath(path: string): string | null {
  if (!path.startsWith(AGENTS_DIR) || !path.endsWith(".md")) return null;
  const name = path.slice(AGENTS_DIR.length, -".md".length);
  return AGENT_NAME_RE.test(name) ? name : null;
}

/** The new file: front matter Claude Code reads, and a prompt body to fill in. No `model:`; the
 *  stage's Model field decides. */
export function agentStarter(name: string): string {
  return [
    "---",
    `name: ${name}`,
    "description: <One line: when Claude should use this agent.>",
    "---",
    `You are ${name}. <What this agent does and how it works.>`,
    "",
  ].join("\n");
}

/** The bundle already has this agent's file. */
export function agentExists(files: readonly { path: string }[], name: string): boolean {
  const path = agentPath(name);
  return files.some((f) => f.path === path);
}
