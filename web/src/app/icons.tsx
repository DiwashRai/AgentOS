// Shell icons, ported from kit.js `I` (18px, stroke 1.5; small ones 12–14px). Decorative only:
// the control that holds one carries the label.

const stroke18 = {
  width: 18,
  height: 18,
  viewBox: "0 0 18 18",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.5,
  "aria-hidden": true,
} as const;

export function TeamsIcon() {
  return (
    <svg {...stroke18}>
      <rect x="3" y="3" width="5" height="5" rx="1.2" />
      <rect x="10" y="3" width="5" height="5" rx="1.2" />
      <rect x="3" y="10" width="5" height="5" rx="1.2" />
      <rect x="10" y="10" width="5" height="5" rx="1.2" />
    </svg>
  );
}

export function QueueIcon() {
  return (
    <svg {...stroke18}>
      <path d="M3 4.5h12M3 9h7M3 13.5h7" />
      <circle cx="13.5" cy="12" r="1.8" />
      <path d="M11 16c.4-1.2 1.4-1.8 2.5-1.8s2.1.6 2.5 1.8" />
    </svg>
  );
}

export function CompactIcon() {
  return (
    <svg {...stroke18}>
      <path d="M3 4h12M3 7.3h12M3 10.6h12M3 14h12" />
    </svg>
  );
}

/** A tray (the pack's "sources" icon): Intake. */
export function IntakeIcon() {
  return (
    <svg {...stroke18}>
      <path d="M3 11.5l2.5-7h7l2.5 7M3 11.5v3h12v-3M3 11.5h3.5l1 1.5h3l1-1.5H15" />
    </svg>
  );
}

/** "Opens elsewhere" (↗), 10px: links out to Jira. */
export function OpenIcon() {
  return (
    <svg
      width="10"
      height="10"
      viewBox="0 0 12 12"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      aria-hidden="true"
    >
      <path d="M5 2.5H2.5v7h7V7M7 2.5h2.5V5M9.5 2.5L5.5 6.5" />
    </svg>
  );
}

export function SettingsIcon() {
  return (
    <svg {...stroke18}>
      <circle cx="9" cy="9" r="2.3" />
      <path d="M9 2.5v2M9 13.5v2M2.5 9h2M13.5 9h2M4.4 4.4l1.4 1.4M12.2 12.2l1.4 1.4M4.4 13.6l1.4-1.4M12.2 5.8l1.4-1.4" />
    </svg>
  );
}

export function ThemeIcon() {
  return (
    <svg {...stroke18}>
      <circle cx="9" cy="9" r="6.2" />
      <path d="M9 2.8a6.2 6.2 0 0 1 0 12.4z" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function CheckIcon() {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 12 12"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      aria-hidden="true"
    >
      <path d="M2.5 6.2l2.3 2.3 4.7-5" />
    </svg>
  );
}

export function SearchIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      aria-hidden="true"
    >
      <circle cx="6" cy="6" r="4" />
      <path d="M9 9l3 3" />
    </svg>
  );
}

export function CloseIcon() {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 12 12"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      aria-hidden="true"
    >
      <path d="M3 3l6 6M9 3l-6 6" />
    </svg>
  );
}
