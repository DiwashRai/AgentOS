import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { Manifest } from "./manifest";
import { ManifestBody } from "./StageCalls";

const PROMPT = "Review the change.\nSECRET-LOOKING-PROMPT-BODY";

const MANIFEST: Manifest = {
  protocol: 2,
  run_id: 7,
  ticket: { key: "AOS-1", title: "A ticket" },
  workspace: {
    mode: "repo",
    repo_url: null,
    branch: null,
    base_branch: null,
    protect: [],
    blocks: [],
  },
  limits: { max_file_bytes: 1, max_line_bytes: 1 },
  context: [
    {
      path: ".claude/agents/reviewer.md",
      kind: "agent",
      name: "reviewer",
      mode: "replace",
      block: null,
      access: "write",
      content: "FILE-CONTENT",
    },
  ],
  notes: [
    { path: ".claude/skills/aos-1/learnings.log", name: "learnings.log", content: "NOTE-CONTENT" },
  ],
  sync_back: { dir: ".agentos/sync_back", max_bytes: 1 },
  env: {},
  stages: [
    {
      id: "review",
      name: "Review",
      kind: "agent",
      timeout_s: 1800,
      check: false,
      verdict_repairs: 0,
      argv: ["claude", "-p", "--agent", "reviewer", "--model", "claude-haiku-5-5"],
      stdin: PROMPT,
    },
    {
      id: "build",
      name: "Build",
      kind: "agent",
      timeout_s: 600,
      check: false,
      verdict_repairs: 0,
      argv: ["copilot", "-p", PROMPT],
      stdin: null,
    },
    {
      id: "test",
      name: "Test",
      kind: "shell",
      timeout_s: 60,
      command: "npm test",
      check: false,
      verdict_repairs: 0,
    },
  ],
};

describe("ManifestBody", () => {
  const html = renderToStaticMarkup(<ManifestBody manifest={MANIFEST} />);

  it("shows each stage's chips, command and prompt fold-out", () => {
    expect(html).toContain("Agent</span>");
    expect(html).toContain("reviewer");
    expect(html).toContain("claude-haiku-5-5");
    expect(html).toContain("Prompt (stdin)");
    expect(html).toContain("‹prompt›");
    expect(html).toContain("npm test");
  });

  it("shows the manifest's warnings and a loop stage's later calls", () => {
    const stage = MANIFEST.stages[0];
    if (!stage) throw new Error("fixture");
    const loop = renderToStaticMarkup(
      <ManifestBody
        manifest={{
          ...MANIFEST,
          warnings: ["LOOP-WARNING"],
          stages: [
            {
              ...stage,
              check: true,
              verdict_repairs: 2,
              verdict_from: "structured_output",
              repairs: [{ argv: ["claude"], stdin: "REPAIR-PROMPT", prompt_arg: null, tail: "" }],
            },
          ],
        }}
      />,
    );
    expect(loop).toContain("LOOP-WARNING");
    expect(loop).toContain("REPAIR-PROMPT");
    expect(html).not.toContain("REPAIR-PROMPT");
  });

  it("lists context entries and notes with their kind, without their contents", () => {
    const list = /<ul[^>]*aria-label="Files given to the agent"[^>]*>(.*?)<\/ul>/.exec(html)?.[1];
    const items = [...(list ?? "").matchAll(/<li>(.*?)<\/li>/g)].map((m) =>
      (m[1] ?? "").replace(/<[^>]+>/g, ""),
    );
    expect(items).toEqual([
      ".claude/agents/reviewer.md · agent",
      ".claude/skills/aos-1/learnings.log · note",
    ]);
    expect(html).not.toContain("FILE-CONTENT");
    expect(html).not.toContain("NOTE-CONTENT");
  });
});
