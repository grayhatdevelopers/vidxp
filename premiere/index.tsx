import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "./src/ui/styles.css";
import { App } from "./src/ui/App";
import { createPremiereAdapter } from "./src/premiere/adapter";
import { installPremiereTheme } from "./src/ui/theme";

// UXP supplies this module inside Premiere at runtime.
// eslint-disable-next-line @typescript-eslint/no-require-imports
const { entrypoints } = require("uxp") as typeof import("uxp");
// eslint-disable-next-line @typescript-eslint/no-require-imports
const os = require("os") as typeof import("os");

installPremiereTheme(document, os.platform());

const rootElement = document.getElementById("root");

if (!rootElement) {
  throw new Error("The VidXP panel root element was not found.");
}

createRoot(rootElement).render(
  <StrictMode>
    <App premiere={createPremiereAdapter()} />
  </StrictMode>,
);

entrypoints.setup({
  panels: {
    // @ts-expect-error Adobe currently declares panels as an array even though
    // runtime panel entrypoints are keyed by their manifest ID.
    vidxpSearch: {
      show() {
        // React owns the panel DOM for the plugin context lifetime.
      },
      hide() {
        // Correctness does not depend on this host lifecycle hook.
      },
    },
  },
});
