import { describe, expect, it } from "vitest";

import { linkify } from "./linkify";

describe("linkify", () => {
  it("returns plain text as one segment", () => {
    expect(linkify("no links here")).toEqual([{ kind: "text", text: "no links here" }]);
    expect(linkify("")).toEqual([]);
  });

  it("links http and https URLs in place", () => {
    expect(linkify("see https://example.com/a?b=1#c and http://x.io now")).toEqual([
      { kind: "text", text: "see " },
      { kind: "link", text: "https://example.com/a?b=1#c", href: "https://example.com/a?b=1#c" },
      { kind: "text", text: " and " },
      { kind: "link", text: "http://x.io", href: "http://x.io/" },
      { kind: "text", text: " now" },
    ]);
  });

  it("leaves trailing punctuation and unbalanced brackets out of the link", () => {
    expect(linkify("Done: https://a.dev/pr/3.")).toEqual([
      { kind: "text", text: "Done: " },
      { kind: "link", text: "https://a.dev/pr/3", href: "https://a.dev/pr/3" },
      { kind: "text", text: "." },
    ]);
    expect(linkify("(see https://a.dev/x)")[1]).toEqual({
      kind: "link",
      text: "https://a.dev/x",
      href: "https://a.dev/x",
    });
    expect(linkify("https://en.wikipedia.org/wiki/Foo_(bar)")[0]).toMatchObject({
      kind: "link",
      text: "https://en.wikipedia.org/wiki/Foo_(bar)",
    });
  });

  it("never links other schemes and keeps markup as text", () => {
    expect(linkify("javascript:alert(1) ftp://x.io")).toEqual([
      { kind: "text", text: "javascript:alert(1) ftp://x.io" },
    ]);
    expect(linkify('<a href="https://x.io">x</a>')).toEqual([
      { kind: "text", text: '<a href="' },
      { kind: "link", text: "https://x.io", href: "https://x.io/" },
      { kind: "text", text: '">x</a>' },
    ]);
  });

  it("skips a bare scheme", () => {
    expect(linkify("http://.")).toEqual([{ kind: "text", text: "http://." }]);
  });
});
