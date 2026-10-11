import { describe, expect, it } from "vitest";
import { avatarSlot, initials } from "./initials";

describe("initials", () => {
  it("takes first and last initials", () => {
    expect(initials("Diwash Rai")).toBe("DR");
    expect(initials("Maya van der Berg")).toBe("MB");
  });
  it("takes two letters of a single name", () => {
    expect(initials("kofi")).toBe("KO");
    expect(initials("K")).toBe("K");
  });
  it("splits logins and emails", () => {
    expect(initials("diwash.rai@example.com")).toBe("DR");
    expect(initials("sam_okoro")).toBe("SO");
  });
  it("never returns empty", () => {
    expect(initials("  ")).toBe("?");
  });
  it("keeps whole characters", () => {
    expect(initials("Élodie Ünal")).toBe("ÉÜ");
  });
});

describe("avatarSlot", () => {
  it("cycles 1..4 by id", () => {
    expect([0, 1, 2, 3, 4, 5].map(avatarSlot)).toEqual([1, 2, 3, 4, 1, 2]);
    expect(avatarSlot(-1)).toBe(4);
  });
});
