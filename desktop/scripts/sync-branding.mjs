import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const source = resolve(desktopRoot, "../plugins/vidxp/assets/logo.png");
const publicDirectory = resolve(desktopRoot, "public");
const favicon = resolve(publicDirectory, "icon.png");
const artifactLogo = resolve(
  desktopRoot,
  "../src/vidxp/assets/artifact_download/vidxp-logo.png",
);
const generatedIcon = resolve(desktopRoot, "src-tauri/icons/128x128.png");

mkdirSync(publicDirectory, { recursive: true });
copyFileSync(source, favicon);
copyFileSync(generatedIcon, artifactLogo);
console.log("Synced VidXP branding from the plugin logo.");
