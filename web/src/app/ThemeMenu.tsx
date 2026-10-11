// The rail's theme button + menu (DESIGN §4 "Theme button", kit.js AOPalette.switcher).
// Click / Enter / Space toggles; ↑ ↓ Home End move; Enter / Space apply; Esc closes and refocuses
// the button; Tab or an outside click closes.
import { type KeyboardEvent, useEffect, useId, useRef, useState } from "react";

import { CheckIcon, ThemeIcon } from "./icons";
import { applyTheme, currentTheme, THEMES, type ThemeId } from "./theme";
import s from "./ThemeMenu.module.css";

export function ThemeMenu() {
  const menuId = useId();
  const [open, setOpen] = useState(false);
  const [theme, setTheme] = useState<ThemeId>(currentTheme);
  const wrapRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([]);

  // On open, focus the selected item.
  useEffect(() => {
    if (!open) return;
    const selected = currentTheme();
    const i = THEMES.findIndex((t) => t.id === selected);
    itemRefs.current[Math.max(0, i)]?.focus();
  }, [open]);

  // An outside click closes (without stealing focus back).
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (e.target instanceof Node && wrapRef.current?.contains(e.target)) return;
      setOpen(false);
    };
    document.addEventListener("pointerdown", onDown);
    return () => {
      document.removeEventListener("pointerdown", onDown);
    };
  }, [open]);

  const close = (refocus: boolean) => {
    setOpen(false);
    if (refocus) buttonRef.current?.focus();
  };

  const choose = (id: ThemeId) => {
    applyTheme(id);
    setTheme(id);
    close(true);
  };

  const onMenuKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const items = itemRefs.current.filter((el): el is HTMLButtonElement => el !== null);
    const i = items.findIndex((el) => el === document.activeElement);
    const go = (n: number) => {
      e.preventDefault();
      e.stopPropagation();
      items[(n + items.length) % items.length]?.focus();
    };
    switch (e.key) {
      case "Escape":
        e.preventDefault();
        e.stopPropagation();
        close(true);
        break;
      case "ArrowDown":
        go(i + 1);
        break;
      case "ArrowUp":
        go(i < 0 ? items.length - 1 : i - 1);
        break;
      case "Home":
        go(0);
        break;
      case "End":
        go(items.length - 1);
        break;
      case "Tab":
        close(false);
        break;
    }
  };

  return (
    <div className={s.wrap} ref={wrapRef}>
      <button
        ref={buttonRef}
        type="button"
        className={s.button}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={menuId}
        aria-label="Theme"
        title="Theme"
        onClick={() => {
          setOpen((o) => !o);
        }}
      >
        <ThemeIcon />
      </button>
      <div
        id={menuId}
        className={s.menu}
        role="menu"
        aria-label="Theme"
        tabIndex={-1}
        hidden={!open}
        onKeyDown={onMenuKeyDown}
      >
        <div className={s.head} aria-hidden="true">
          Theme
        </div>
        {THEMES.map((t, i) => (
          <button
            key={t.id}
            ref={(el) => {
              itemRefs.current[i] = el;
            }}
            type="button"
            className={s.item}
            role="menuitemradio"
            aria-checked={t.id === theme}
            tabIndex={-1}
            title={t.mood}
            onClick={() => {
              choose(t.id);
            }}
          >
            <span className={s.name}>
              <CheckIcon />
              {t.name}
            </span>
            <span className={s.mode}>{t.mode}</span>
            {/* The swatches carry their own theme, so they read that theme's tokens. */}
            <span className={s.swatches} data-theme={t.id} aria-hidden="true">
              <i className={s.swPage} />
              <i className={s.swInk} />
              <i className={s.swAccent} />
              <i className={s.swWaiting} />
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
