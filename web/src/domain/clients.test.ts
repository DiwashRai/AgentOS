import { describe, expect, it } from "vitest";

import { clientWait } from "./clients";
import { run, ticket } from "./fixtures";

describe("clientWait", () => {
  const now = {
    actor: null,
    person: null,
    progress: null,
    run_id: 1,
    text: "waiting for Sam's client",
  };

  it("reads a queued client run's wait from the server, with its client's state", () => {
    const queued = (client_online: boolean | null) =>
      clientWait(
        ticket({
          attention: "queued",
          now,
          latest_run: run("queued", { target: "client", client_online }),
        }),
      );
    expect(queued(true)).toEqual({ text: now.text, online: "online" });
    expect(queued(false)?.online).toBe("offline");
    expect(queued(null)?.online).toBe("offline");
  });

  it("covers a client run handed out but not claimed yet", () => {
    const handed = run("dispatched", { target: "client", client_online: true });
    expect(clientWait(ticket({ attention: "queued", now, latest_run: handed }))?.online).toBe(
      "online",
    );
  });

  it("is null for a server run, a started client run, and no run", () => {
    const at = (latest_run: ReturnType<typeof run> | null) =>
      clientWait(ticket({ attention: "queued", now, latest_run }));
    expect(at(run("queued"))).toBeNull();
    expect(at(run("dispatched"))).toBeNull();
    expect(at(run("running", { target: "client", client_online: true }))).toBeNull();
    expect(at(null)).toBeNull();
  });
});
