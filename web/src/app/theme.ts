// Themes (tokens/THEMES.md). The attribute is `data-theme` on <html>; index.html applies the saved
// choice before first paint with the same key and values, so keep the two in sync.
export type ThemeId = "light" | "dark";
type ThemeAttr = ThemeId | "company-light" | "company-dark";

export const THEME_KEY = "agentos-theme";

export interface ThemeInfo {
  id: ThemeId;
  name: string;
  mode: string;
  mood: string;
}

/** The menu lists exactly these, in this order. The company themes alias them for now. */
export const THEMES: readonly ThemeInfo[] = [
  {
    id: "light",
    name: "Type Case Cool",
    mode: "Light · default",
    mood: "Type Case on a cleaner desk: neutral, faintly cool greys and crisp white sheets under near-black ink, the same vermilion for act and live, and status that steps clearly in lightness.",
  },
  {
    id: "dark",
    name: "Harbour",
    mode: "Dark · default",
    mood: "Deep harbour-slate after dark under one brass lamp: calm and focused, warm where your eye lands.",
  },
];

const ATTRS: readonly string[] = ["light", "dark", "company-light", "company-dark"];

function isThemeAttr(v: string | null | undefined): v is ThemeAttr {
  return v != null && ATTRS.includes(v);
}

/** The theme the menu shows as selected (a company alias counts as its base theme). */
export function currentTheme(): ThemeId {
  const attr = document.documentElement.dataset.theme;
  if (!isThemeAttr(attr)) return "light";
  return attr.endsWith("dark") ? "dark" : "light";
}

export function applyTheme(id: ThemeId): void {
  document.documentElement.dataset.theme = id;
  try {
    localStorage.setItem(THEME_KEY, id);
  } catch {
    // Storage blocked: the choice lasts for this page only.
  }
}
