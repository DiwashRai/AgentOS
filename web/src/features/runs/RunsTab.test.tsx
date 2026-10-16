import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { SyncBackLine } from "./RunsTab";

describe("SyncBackLine", () => {
  it("links the run's sync_back zip by its name", () => {
    const html = renderToStaticMarkup(
      <SyncBackLine run={{ id: 7, sync_back: { files: 2, bytes: 10 } }} />,
    );
    const links = [...html.matchAll(/<a [^>]*href="([^"]*)"[^>]*>(.*?)<\/a>/g)];
    expect(links).toHaveLength(1);
    expect(links[0]?.[1]).toBe("/api/runs/7/sync-back");
    expect(links[0]?.[2]).toContain("sync_back.zip");
  });

  it("draws nothing when the run returned no files", () => {
    expect(renderToStaticMarkup(<SyncBackLine run={{ id: 7, sync_back: null }} />)).toBe("");
  });
});
