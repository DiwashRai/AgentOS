// Identity hues (visual refresh): a project key chip takes its hue from the key's project prefix,
// a row avatar from the person's id. Each is a stable hash over the five `--cat-*` hues, so the same
// project or person always reads in the same colour, in every view and both themes. Pure.

export const HUES = ["violet", "plum", "rose", "gold", "olive"] as const;
export type Hue = (typeof HUES)[number];

/** FNV-1a (32-bit) over the UTF-16 code units: small, stable and well spread for short strings. */
function fnv1a(text: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/** The hue for any seed string. */
export function hueOf(seed: string): Hue {
  return HUES[fnv1a(seed) % HUES.length] ?? "violet";
}

/** "AOS-14" → "AOS"; "scrum-3" → "SCRUM"; a bare prefix ("AOS") is its own project. */
export function projectPrefix(key: string): string {
  const trimmed = key.trim();
  const dash = trimmed.lastIndexOf("-");
  const head = dash > 0 && /^\d+$/.test(trimmed.slice(dash + 1)) ? trimmed.slice(0, dash) : trimmed;
  return head.toUpperCase();
}

/** A ticket or project key's hue: by its project prefix, so AOS-14 and AOS-16 match. */
export function keyHue(key: string): Hue {
  return hueOf(`project:${projectPrefix(key)}`);
}

/** A person's hue, by their id. */
export function personHue(id: number): Hue {
  return hueOf(`person:${String(Math.trunc(id))}`);
}
