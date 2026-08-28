import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const projectRoot = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig(({ mode }) => {
  const cep = mode === "cep";
  return {
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
              assetFileNames: "[name][extname]"
            }
          }
        }
      : {
          outDir: "dist/uxp",
          emptyOutDir: true,
          minify: false,
          sourcemap: true,
          target: "esnext",
          rolldownOptions: {
            input: resolve(projectRoot, "index.tsx"),
            external: ["os", "premierepro", "uxp"],
            output: {
              format: "cjs",
              preserveModules: true,
              preserveModulesRoot: projectRoot,
              entryFileNames: "[name].js"
            }
          }
        },
    test: {
      environment: "node",
      include: ["tests/**/*.test.ts"]
    }
  };
});
