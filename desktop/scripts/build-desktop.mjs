import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync, rmSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const bundleSpecs = {
  darwin: {
    bundle: "dmg",
    directory: "dmg",
    suffix: ".dmg",
    filename: (version) => `VidXP_${version}_aarch64.dmg`,
  },
  linux: {
    bundle: "appimage",
    directory: "appimage",
    suffix: ".AppImage",
    filename: (version) => `VidXP_${version}_amd64.AppImage`,
  },
  win32: {
    bundle: "nsis",
    directory: "nsis",
    suffix: "-setup.exe",
    filename: (version) => `VidXP_${version}_x64-setup.exe`,
  },
};

const bundleSpec = bundleSpecs[process.platform];
if (!bundleSpec) {
  throw new Error(`Unsupported desktop build platform: ${process.platform}`);
}

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const bundleRoot = resolve(desktopRoot, "src-tauri", "target", "release", "bundle");
rmSync(bundleRoot, { force: true, recursive: true });

const executable = resolve(
  desktopRoot,
  "node_modules",
  ".bin",
  process.platform === "win32" ? "tauri.cmd" : "tauri",
);

// Sign + notarize only when explicitly requested on macOS (release builds).
const signMacos =
  process.platform === "darwin" && process.env.VIDXP_DESKTOP_SIGN === "1";

const buildArgs = ["build", "--bundles", bundleSpec.bundle, "--ci"];
if (!signMacos) {
  buildArgs.push("--no-sign");
}
buildArgs.push("--", "--locked");

const result = spawnSync(executable, buildArgs, {
  shell: process.platform === "win32",
  stdio: "inherit",
});

if (result.error) {
  throw result.error;
}
if (result.status !== 0) {
  process.exit(result.status ?? 1);
}

const { version } = JSON.parse(
  readFileSync(resolve(desktopRoot, "package.json"), "utf8"),
);
const outputDirectory = resolve(bundleRoot, bundleSpec.directory);
const expectedArtifact = resolve(outputDirectory, bundleSpec.filename(version));
const packagedArtifacts = existsSync(outputDirectory)
  ? readdirSync(outputDirectory, { withFileTypes: true })
      .filter(
        (entry) => entry.isFile() && entry.name.endsWith(bundleSpec.suffix),
      )
      .map((entry) => entry.name)
  : [];
if (
  !existsSync(expectedArtifact) ||
  packagedArtifacts.length !== 1 ||
  packagedArtifacts[0] !== bundleSpec.filename(version)
) {
  throw new Error(
    `Expected only ${expectedArtifact}, found: ${packagedArtifacts.join(", ") || "none"}`,
  );
}
