import { copyFileSync, mkdirSync, readdirSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { spawnSync } from "node:child_process";

const require = createRequire(import.meta.url);
const root = resolve(import.meta.dirname, "..");
const packages = resolve(root, "packages");
const uxpPackage = resolve(packages, "vidxp-premiere-uxp.ccx");
const cepPackage = resolve(packages, "vidxp-premiere-cep.zxp");
const certificate = resolve(packages, "vidxp-premiere-build-certificate.p12");

mkdirSync(packages, { recursive: true });
const generatedUxpPackages = readdirSync(resolve(root, "ccx"))
  .filter((name) => name.endsWith(".ccx"));
if (generatedUxpPackages.length !== 1) {
  throw new Error(`Expected one Bolt UXP package, found ${generatedUxpPackages.length}.`);
}
copyFileSync(
  resolve(root, "ccx", generatedUxpPackages[0]),
  uxpPackage,
);

const signerRoot = resolve(require.resolve("zxp-signer/package.json"), "..");
const signerPlatform = process.platform === "win32"
  ? process.arch === "x64" ? "win64" : "Win32"
  : "osx";
const signer = resolve(signerRoot, "bin", "4.1.3", signerPlatform, `ZXPSignCmd${process.platform === "win32" ? ".exe" : ""}`);
const certificatePassword = process.env.ZXP_CERT_PASSWORD || "vidxp-release-build";
runSigner([
  "-selfSignedCert",
  process.env.ZXP_CERT_COUNTRY || "PK",
  process.env.ZXP_CERT_PROVINCE || "Punjab",
  process.env.ZXP_CERT_ORG || "Grayhat Developers PVT Ltd",
  process.env.ZXP_CERT_NAME || "org.grayhat.vidxp-premiere.cep",
  certificatePassword,
  certificate,
]);
const result = runSigner([
  "-sign",
  resolve(root, "dist", "cep"),
  cepPackage,
  certificate,
  certificatePassword,
  "-tsa",
  process.env.ZXP_TIMESTAMP || "http://timestamp.digicert.com/",
], false);
rmSync(certificate, { force: true });
if (result.status !== 0) process.exit(result.status ?? 1);

function runSigner(arguments_, exitOnFailure = true) {
  const result = spawnSync(signer, arguments_, { stdio: "inherit" });
  if (exitOnFailure && result.status !== 0) process.exit(result.status ?? 1);
  return result;
}
