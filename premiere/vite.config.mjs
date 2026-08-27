import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const projectRoot = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  publicDir: "public",
  base: "./",
  build: {
    outDir: "dist",
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
});
