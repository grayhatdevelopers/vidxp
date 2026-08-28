import { resolve } from "node:path";
import { defineConfig } from "vite";
import { uxp } from "vite-uxp-plugin";

import { config as uxpConfig } from "./uxp.config.ts";

const projectRoot = import.meta.dirname;

export default defineConfig(({ mode }) => {
  const cep = mode === "cep";
  const boltMode = process.env.BOLT_MODE;
  return {
    plugins: cep ? [] : [uxp(uxpConfig, boltMode)],
    publicDir: cep ? false : "public",
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
            input: resolve(projectRoot, "index.tsx"),
            external: ["os", "premierepro", "uxp"],
            output: {
              format: "iife",
              entryFileNames: "index.js",
              assetFileNames: "[name][extname]",
            },
          },
        },
    test: {
      environment: "node",
      include: ["tests/**/*.test.ts"],
    },
  };
});
