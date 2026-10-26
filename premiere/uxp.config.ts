import { readFileSync } from "node:fs";
import type { UXP_Config, UXP_Manifest } from "vite-uxp-plugin";

const hotReloadPort = 8080;
const development = process.env.BOLT_MODE === "dev";
const packageJson = JSON.parse(
  readFileSync(new URL("./package.json", import.meta.url), "utf8"),
) as { version: string };

const manifest: UXP_Manifest = {
  manifestVersion: 5,
  id: "org.grayhat.vidxp-premiere",
  name: "VidXP Search",
  version: packageJson.version,
  main: "index.html",
  host: [
    {
      app: "premierepro",
      minVersion: "25.6.0",
    },
  ],
  requiredPermissions: {
    network: {
      domains: [
        "http://127.0.0.1",
        "http://localhost",
        "https://127.0.0.1",
        "https://localhost",
        ...(development ? [`ws://localhost:${hotReloadPort}`] : []),
      ],
    },
  },
  entrypoints: [
    {
      type: "panel",
      id: "vidxpSearch",
      label: {
        default: "VidXP Search",
      },
      minimumSize: {
        width: 320,
        height: 480,
      },
      maximumSize: {
        width: 1800,
        height: 1800,
      },
      preferredDockedSize: {
        width: 380,
        height: 720,
      },
      preferredFloatingSize: {
        width: 480,
        height: 760,
      },
    },
  ],
};

export const config: UXP_Config = {
  manifest,
  hotReloadPort,
  webviewUi: false,
  webviewReloadPort: 8082,
  copyZipAssets: [],
  uniqueIds: false,
  debugger: "udt",
};
