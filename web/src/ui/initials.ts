// Pure helpers for Avatar.

const graphemes = new Intl.Segmenter(undefined, { granularity: "grapheme" });

/** The first `n` user-perceived characters of a word. */
function head(word: string, n: number): string {
  let out = "";
  let i = 0;
  for (const { segment } of graphemes.segment(word)) {
    if (i++ >= n) break;
    out += segment;
  }
  return out;
}

/** "Diwash Rai" → "DR"; "maya" → "MA"; "diwash.rai@x.io" → "DR". Kit people carry two-letter initials. */
export function initials(name: string): string {
  const local = name.trim().split("@")[0] ?? "";
  const words = local.split(/[\s._-]+/).filter(Boolean);
  const first = words[0];
  const last = words.length > 1 ? words[words.length - 1] : undefined;
  if (!first) return "?";
  return (last ? head(first, 1) + head(last, 1) : head(first, 2)).toUpperCase();
}

/** Avatar colour slot 1..4 (`--av-1..4`), stable per person id. */
export function avatarSlot(id: number): 1 | 2 | 3 | 4 {
  const i = ((Math.trunc(id) % 4) + 4) % 4;
  return (i + 1) as 1 | 2 | 3 | 4;
}
