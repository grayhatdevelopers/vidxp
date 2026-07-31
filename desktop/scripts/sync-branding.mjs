import { copyFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const source = resolve(desktopRoot, "../docs/images/logo.png");
const favicon = resolve(desktopRoot, "web/icon.png");

copyFileSync(source, favicon);
console.log("Synced the VidXP desktop favicon from the shared icon.");
