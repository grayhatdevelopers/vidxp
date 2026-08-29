import { cpSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const output = resolve(root, "dist", "cep");

mkdirSync(resolve(output, "CSXS"), { recursive: true });
mkdirSync(resolve(output, "jsx"), { recursive: true });
cpSync(resolve(root, "cep", "index.html"), resolve(output, "index.html"));
cpSync(resolve(root, "cep", "CSXS", "manifest.xml"), resolve(output, "CSXS", "manifest.xml"));
cpSync(resolve(root, "cep", "jsx", "host.jsx"), resolve(output, "jsx", "host.jsx"));
cpSync(resolve(root, "src", "ui", "styles.css"), resolve(output, "styles.css"));
