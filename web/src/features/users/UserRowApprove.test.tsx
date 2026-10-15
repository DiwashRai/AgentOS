// @vitest-environment jsdom
// Approve's click patches the role to writer (the markup tests can't click).
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { User } from "./model";
import { UserRow } from "./UserRow";

const ana: User = {
  id: 7,
  display_name: "Ana Ruiz",
  email: "ana@example.com",
  avatar_url: null,
  role: "pending",
  disabled: false,
  env_admin: false,
  created_at: "2026-10-11T09:00:00Z",
  last_login_at: "2026-10-11T09:00:00Z",
};

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement | null = null;

afterEach(() => {
  host?.remove();
  host = null;
});

function mount(onPatch: (patch: { role?: User["role"]; disabled?: boolean }) => void) {
  host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  act(() => {
    root.render(
      <table>
        <UserRow
          user={ana}
          self={false}
          now={Date.parse("2026-10-11T12:00:00Z")}
          pending={false}
          errors={[]}
          onPatch={onPatch}
        />
      </table>,
    );
  });
  return { root, host };
}

describe("UserRow Approve", () => {
  it("patches role writer", () => {
    const onPatch = vi.fn();
    const { root, host } = mount(onPatch);
    const approve = host.querySelector<HTMLButtonElement>('button[aria-label="Approve Ana Ruiz"]');
    expect(approve).not.toBeNull();
    act(() => {
      approve?.click();
    });
    expect(onPatch).toHaveBeenCalledExactlyOnceWith({ role: "writer" });
    act(() => {
      root.unmount();
    });
  });
});
