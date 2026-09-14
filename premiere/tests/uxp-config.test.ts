import { describe, expect, it } from "vitest";

import { config } from "../uxp.config";

describe("Bolt UXP configuration", () => {
  it("targets the supported Premiere UXP host with a production manifest", () => {
    expect(config.manifest.manifestVersion).toBe(5);
    expect(config.manifest.host).toEqual([
      { app: "premierepro", minVersion: "25.6.0" },
    ]);
    expect(config.manifest.entrypoints).toContainEqual(
      expect.objectContaining({ type: "panel", id: "vidxpSearch" }),
    );
    expect(config.manifest.requiredPermissions?.network?.domains).not.toContain(
      "ws://localhost:8080",
    );
    expect(config.webviewUi).toBe(false);
    expect(config.uniqueIds).toBe(false);
    expect(config.debugger).toBe("udt");
  });
});
