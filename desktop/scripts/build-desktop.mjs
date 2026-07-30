import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const bundles = {
  darwin: "dmg",
  linux: "appimage",
  win32: "nsis",
};

const bundle = bundles[process.platform];
if (!bundle) {
  throw new Error(`Unsupported desktop build platform: ${process.platform}`);
}

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const executable = resolve(
  desktopRoot,
  "node_modules",
  ".bin",
  process.platform === "win32" ? "tauri.cmd" : "tauri",
);
const result = spawnSync(
  executable,
  ["build", "--bundles", bundle, "--ci", "--no-sign", "--", "--locked"],
  { shell: process.platform === "win32", stdio: "inherit" },
);

if (result.error) {
  throw result.error;
}
process.exit(result.status ?? 1);
