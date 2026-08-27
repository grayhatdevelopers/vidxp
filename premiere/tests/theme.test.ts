import { describe, expect, it, vi } from "vitest";

import {
  installPremiereTheme,
  premiereThemeClass,
} from "../src/ui/theme";

describe("Premiere theme integration", () => {
  it("maps Premiere light themes and dark variants", () => {
    expect(premiereThemeClass("light")).toBe("theme-light");
    expect(premiereThemeClass("darkest")).toBe("theme-dark");
  });

  it("applies updates and removes its listener during cleanup", () => {
    const classes = new Set<string>();
    let listener: ((theme: string) => void) | undefined;
    const addListener = vi.fn((next: (theme: string) => void) => {
      listener = next;
    });
    const removeListener = vi.fn();
    const cleanup = installPremiereTheme({
      body: {
        classList: {
          add: (...tokens) => tokens.forEach((token) => classes.add(token)),
          remove: (...tokens) => tokens.forEach((token) => classes.delete(token)),
        },
      },
      theme: {
        getCurrent: () => "dark",
        onUpdated: { addListener, removeListener },
      },
    });

    expect(classes).toEqual(new Set(["theme-dark"]));
    listener?.("light");
    expect(classes).toEqual(new Set(["theme-light"]));

    cleanup();
    expect(removeListener).toHaveBeenCalledOnce();
  });
});
