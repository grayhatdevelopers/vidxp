import type { PremiereAdapter, PremiereLibrary } from "./types";

interface CepBridge {
  evalScript(script: string, callback: (result: string) => void): void;
}

interface BridgeEnvelope<T> {
  ok: boolean;
  value?: T;
  error?: string;
}

export function createCepPremiereAdapter(): PremiereAdapter {
  return {
    getLibrary: () => evaluate<PremiereLibrary | undefined>("$._VIDXP.getLibrary()"),
    getSelectedProjectItemIds: () =>
      evaluate<string[]>("$._VIDXP.getSelection()"),
  };
}

function evaluate<T>(script: string): Promise<T> {
  const bridge = Reflect.get(window, "__adobe_cep__") as CepBridge | undefined;
  if (!bridge) return Promise.reject(new Error("Premiere's CEP bridge is unavailable."));

  return new Promise((resolve, reject) => {
    bridge.evalScript(script, (rawResult) => {
      if (!rawResult || rawResult === "EvalScript error.") {
        reject(new Error("Premiere could not run the VidXP host bridge."));
        return;
      }
      try {
        const envelope = JSON.parse(rawResult) as BridgeEnvelope<T>;
        if (!envelope.ok) {
          reject(new Error(envelope.error || "Premiere returned an unknown error."));
          return;
        }
        resolve(envelope.value as T);
      } catch {
        reject(new Error("Premiere returned an invalid VidXP host response."));
      }
    });
  });
}
