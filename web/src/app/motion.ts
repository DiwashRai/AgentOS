// Pause every CSS animation while the tab is hidden (DESIGN §14). base.css reads `html.ao-paused`.
export function watchVisibility(): void {
  const sync = () => {
    document.documentElement.classList.toggle("ao-paused", document.hidden);
  };
  document.addEventListener("visibilitychange", sync);
  sync();
}
