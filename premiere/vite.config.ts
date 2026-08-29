import { resolve } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { runAction, uxp } from "vite-uxp-plugin";

import { config as uxpConfig } from "./uxp.config.ts";

const projectRoot = import.meta.dirname;
const action = process.env.BOLT_ACTION;

if (action) runAction(uxpConfig, action);

export default defineConfig(({ mode }) => {
  const cep = mode === "cep";
  const boltMode = process.env.BOLT_MODE;
  return {
    plugins: cep ? [react()] : [uxp(uxpConfig, boltMode), react()],
    publicDir: false,
    base: "./",
    build: cep
      ? {
          outDir: "dist/cep",
          emptyOutDir: true,
          minify: true,
          sourcemap: true,
          target: "chrome88",
          rolldownOptions: {
            input: resolve(projectRoot, "cep/index.tsx"),
            output: {
              format: "iife",
              entryFileNames: "index.js",
              assetFileNames: "[name][extname]",
            },
          },
        }
      : {
          // Bolt's CCX generator packages this project-root directory.
          outDir: "dist",
          emptyOutDir: true,
          minify: false,
          sourcemap: boltMode === "package" ? false : "inline",
          target: "esnext",
          rolldownOptions: {
            external: ["os", "premierepro", "uxp"],
            output: {
              format: "iife",
              entryFileNames: "index.js",
              assetFileNames: (asset) =>
                asset.names.some((name) => name.endsWith(".css"))
                  ? "styles.css"
                  : "[name][extname]",
            },
          },
        },
    test: {
      environment: "node",
      include: ["tests/**/*.test.ts"],
    },
  };
});
