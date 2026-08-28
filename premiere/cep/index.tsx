import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { createCepPremiereAdapter } from "../src/premiere/cep-adapter";
import { createCepFetch } from "../src/services/vidxp/cep-fetch";
import { App } from "../src/ui/App";

Reflect.set(window, "__VIDXP_CEP__", true);

const rootElement = document.getElementById("root");
if (!rootElement) throw new Error("The VidXP panel root element was not found.");

createRoot(rootElement).render(
  <StrictMode>
    <App fetchImpl={createCepFetch()} premiere={createCepPremiereAdapter()} />
  </StrictMode>,
);
