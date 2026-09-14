import { afterEach, describe, expect, it } from "vitest";

import { createCepPremiereAdapter } from "../src/premiere/cep-adapter";

describe("CEP Premiere adapter", () => {
  afterEach(() => {
    Reflect.deleteProperty(globalThis, "window");
  });

  it("reads the shared library contract from ExtendScript", async () => {
    Reflect.set(globalThis, "window", {
      __adobe_cep__: {
        evalScript(script: string, callback: (value: string) => void) {
          expect(script).toBe("$._VIDXP.getLibrary()");
          callback(JSON.stringify({
            ok: true,
            value: { projectName: "Cut", items: [] },
          }));
        },
      },
    });

    await expect(createCepPremiereAdapter().getLibrary()).resolves.toEqual({
      projectName: "Cut",
      items: [],
    });
  });

  it("turns a bounded host error into a rejected operation", async () => {
    Reflect.set(globalThis, "window", {
      __adobe_cep__: {
        evalScript(_script: string, callback: (value: string) => void) {
          callback(JSON.stringify({ ok: false, error: "No project is open." }));
        },
      },
    });

    await expect(createCepPremiereAdapter().getSelectedProjectItemIds())
      .rejects.toThrow("No project is open.");
  });
});
