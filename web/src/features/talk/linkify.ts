// Talk is plain text: split a message into text and http(s) link segments, rendered as React
// text nodes and <a> elements (never as HTML).

export type Segment = { kind: "text"; text: string } | { kind: "link"; text: string; href: string };

const URL_RE = /\bhttps?:\/\/[^\s<>"']+/gi;
const TRAILING = new Set([".", ",", ";", ":", "!", "?"]);
const PAIRS: Record<string, string> = { ")": "(", "]": "[", "}": "{" };

function count(s: string, ch: string): number {
  return s.split(ch).length - 1;
}

/** Drop sentence punctuation and unbalanced closing brackets from the end of a matched URL. */
function trimUrl(url: string): string {
  let out = url;
  for (;;) {
    const last = out.slice(-1);
    const open = PAIRS[last];
    if (TRAILING.has(last)) out = out.slice(0, -1);
    else if (open !== undefined && count(out, open) < count(out, last)) out = out.slice(0, -1);
    else return out;
  }
}

/** A valid absolute http(s) URL, else null. */
function safeHref(url: string): string | null {
  try {
    const u = new URL(url);
    return u.protocol === "http:" || u.protocol === "https:" ? u.href : null;
  } catch {
    return null;
  }
}

export function linkify(text: string): Segment[] {
  const out: Segment[] = [];
  let from = 0;
  const push = (t: string) => {
    if (!t) return;
    const prev = out[out.length - 1];
    if (prev?.kind === "text") prev.text += t;
    else out.push({ kind: "text", text: t });
  };
  for (const match of text.matchAll(URL_RE)) {
    const url = trimUrl(match[0]);
    const href = safeHref(url);
    if (href === null) continue;
    push(text.slice(from, match.index));
    out.push({ kind: "link", text: url, href });
    from = match.index + url.length;
  }
  push(text.slice(from));
  return out;
}
