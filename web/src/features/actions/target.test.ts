import { describe, expect, it } from "vitest";

import { person } from "../../domain/fixtures";
import { chosenTarget, targetChoice, targetOptions } from "./target";

const me = { id: 1 };
const mine = { owner: person(1) };
const theirs = { owner: person(2) };
const unowned = { owner: null };
const online = { online: true };
const offline = { online: false };

describe("the dispatch target rule", () => {
  it.each([
    ["my ticket, a client online", mine, me, [offline, online], true, "client"],
    ["my ticket, every client offline", mine, me, [offline], true, "server"],
    ["my ticket, no tokens", mine, me, [], false, "server"],
    ["my ticket, tokens still loading", mine, me, undefined, false, "server"],
    ["someone else's ticket", theirs, me, [online], false, "server"],
    ["an unowned ticket", unowned, me, [online], false, "server"],
    ["signed out", mine, null, [online], false, "server"],
  ] as const)("%s", (_, t, who, clients, offer, target) => {
    const choice = targetChoice(t, who, clients);
    expect(choice.offer).toBe(offer);
    expect(choice.target).toBe(target);
  });

  it("sends the user's pick only while the choice is offered", () => {
    const offered = targetChoice(mine, me, [online]);
    expect(chosenTarget(offered, null)).toBe("client");
    expect(chosenTarget(offered, "server")).toBe("server");

    const hidden = targetChoice(theirs, me, [online]);
    expect(chosenTarget(hidden, "client")).toBe("server");
  });

  it("offers My client first, then the server", () => {
    const values = targetOptions(targetChoice(mine, me, [offline])).map((o) => o.value);
    expect(values).toEqual(["client", "server"]);
  });
});
