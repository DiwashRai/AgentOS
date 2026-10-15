import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { Manifest } from "./manifest";
import { ManifestBody } from "./StageCalls";

const PROMPT = "Review the change.\nSECRET-LOOKING-PROMPT-BODY";

const MANIFEST: Manifest = {
  protocol: 1,
  run_id: 7,
  ticket: { key: "AOS-1", title: "A ticket" },
  workspace: { mode: "repo", repo_url: null, branch: null, base_branch: null },
  limits: { max_file_bytes: 1, max_line_bytes: 1 },
  sync_dirs: [],
  files: [
    { path: ".claude/agents/reviewer.md", content: "FILE-CONTENT", mode: "replace", sync: false },
  ],
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

  it("lists file paths without their contents", () => {
    expect(html).toContain("Files given to the agent (1)");
    expect(html).toContain(".claude/agents/reviewer.md");
    expect(html).not.toContain("FILE-CONTENT");
  });
});
