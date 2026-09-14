import eslint from "@eslint/js";
import premierepro from "@adobe/eslint-plugin-premierepro";
import reactHooks from "eslint-plugin-react-hooks";
import { defineConfig, globalIgnores } from "eslint/config";
import typescript from "typescript-eslint";

export default defineConfig(
  globalIgnores(["dist/**", "ccx/**", "coverage/**"]),
  {
    files: ["index.tsx", "*.config.ts", "src/**/*.{ts,tsx}", "tests/**/*.ts"],
    extends: [
      eslint.configs.recommended,
      ...typescript.configs.recommendedTypeChecked,
      premierepro.configs.recommendedTypeChecked,
      reactHooks.configs.flat.recommended,
    ],
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },
  {
    files: ["tests/**/*.ts"],
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
    },
  },
);
