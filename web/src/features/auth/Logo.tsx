// The app mark on the signed-out sheets (kit .ao-rail__logo).
import s from "./LoginPage.module.css";

export function Logo() {
  return (
    <span className={s.logo} aria-hidden="true">
      <svg width="14" height="14" viewBox="0 0 14 14">
        <circle cx="7" cy="7" r="2.4" style={{ fill: "var(--accent-mark)" }} />
        <circle
          cx="7"
          cy="7"
          r="5.6"
          fill="none"
          style={{ stroke: "var(--bubble-mine-ink)" }}
          strokeWidth="1.3"
        />
      </svg>
    </span>
  );
}
