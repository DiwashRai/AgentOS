// Markup checks via server rendering (node environment; queries don't fetch without effects).
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { Schemas } from "../../api/client";
import { PendingPage } from "./PendingPage";

const sam: Schemas["Me"] = {
  id: 7,
  display_name: "Sam Lee",
  email: "sam@example.com",
  avatar_url: null,
  role: "pending",
};

function render(me: Schemas["Me"]) {
  return renderToStaticMarkup(
    <QueryClientProvider client={new QueryClient()}>
      <PendingPage me={me} />
    </QueryClientProvider>,
  );
}

describe("PendingPage", () => {
  it("says who is waiting, where approval happens, and offers Sign out", () => {
    const html = render(sam);
    expect(html).toContain("Waiting for an admin to approve you");
    expect(html).toContain("Sam Lee");
    expect(html).toContain("sam@example.com");
    expect(html).toContain("Settings › Users");
    expect(html).toContain(">Sign out<");
  });

  it("doesn't repeat an email that is also the display name", () => {
    const html = render({ ...sam, display_name: "sam@example.com" });
    expect(html.split("sam@example.com")).toHaveLength(2);
  });
});
