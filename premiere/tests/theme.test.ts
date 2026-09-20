import { describe, expect, it, vi } from "vitest";

import {
  installPremiereTheme,
  premiereThemeClass,
  premiereThemePalette,
} from "../src/ui/theme";

describe("Premiere theme integration", () => {
  it("maps Premiere light themes and dark variants", () => {
    expect(premiereThemeClass("light")).toBe("theme-light");
    expect(premiereThemeClass("darkest")).toBe("theme-dark");
    expect(premiereThemePalette("darkest", "win32")).toMatchObject({
      "--uxp-host-background-color": "#1D1D1D",
      "--uxp-host-link-text-color": "#0098FA",
    });
    expect(premiereThemePalette("darkest", "darwin")).toMatchObject({
      "--uxp-host-link-text-color": "#4096F3",
    });
  });

  it("applies updates and removes its listener during cleanup", () => {
    const classes = new Set<string>();
    const dataset: Record<string, string> = {};
    const properties = new Map<string, string>();
    let listener: ((theme: string) => void) | undefined;
    const addListener = vi.fn((next: (theme: string) => void) => {
      listener = next;
    });
    const removeListener = vi.fn();
    const cleanup = installPremiereTheme({
      documentElement: {
        dataset,
        style: {
          setProperty: (name, value) => properties.set(name, value),
        },
      },
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
    }, "win32");

    expect(classes).toEqual(new Set(["theme-dark"]));
    expect(dataset).toEqual({ theme: "dark", platform: "win32" });
    expect(properties.get("--uxp-host-background-color")).toBe("#323232");
    listener?.("light");
    expect(classes).toEqual(new Set(["theme-light"]));
    expect(dataset.theme).toBe("light");
    expect(properties.get("--uxp-host-background-color")).toBe("#F8F8F8");

    cleanup();
    expect(removeListener).toHaveBeenCalledOnce();
  });
});
