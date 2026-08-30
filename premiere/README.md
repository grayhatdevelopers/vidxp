# VidXP Premiere Pro extension

This directory builds the VidXP panel for both Premiere extension generations.
The local Uplift repository was used only as a UXP packaging and host-boundary
reference; this is not an Uplift integration.

## Architecture

```text
Shared React workflow (`src/ui`)
├── typed VidXP client (`src/services/vidxp`)
├── shared media-library rules (`src/premiere/library.ts`)
├── Bolt UXP build + Premiere adapter (`uxp.config.ts`, `index.tsx`, `adapter.ts`)
└── CEP bootstrap + Premiere adapter (`cep/`, `cep-adapter.ts`)
    ├── ES3 ExtendScript project bridge (`cep/jsx/host.jsx`)
    └── CEP Node loopback transport (`cep-fetch.ts`)
```

The UI receives a `PremiereAdapter`; it does not import a host API. The UXP
adapter alone imports Premiere's `premierepro` module. The CEP adapter alone
uses `evalScript`, and its ExtendScript bridge returns bounded JSON. Indexing,
polling, grounded queries, status, capability discovery, and selection rules
are shared. Grounded queries use the public durable job contract and preserve
ranked moments when answer generation falls back to evidence-only mode.

UXP renders Adobe's built-in Spectrum widgets through the typed control
wrapper. CEP renders native HTML controls through that same wrapper because
Spectrum UXP widgets do not exist in CEP. React remains at version 19 for both
builds, with CEP compiled for Chromium 88.

Capability names and roles come from `GET /api/v1/capabilities`; neither host
adapter hardcodes dialogue, sound, scene, action, or future capability names.

## Build and package

```bash
npm ci
npm run check
npm run package
```

The UXP target follows Bolt UXP's React scaffold: Vite owns the HTML entry,
`@vitejs/plugin-react` compiles React, and
[Bolt UXP](https://github.com/hyperbrew/bolt-uxp)'s `vite-uxp-plugin` owns
manifest generation, UXP-compatible transforms and polyfills, hot reload, CCX
creation, and package installation actions. Bolt's Premiere-specific host color
variables are initialized before React mounts and update when the host theme
changes. The separate CEP target shares the application code but retains its
own Vite and ZXP signing path because Bolt UXP does not build CEP extensions.

`npm run check` creates the UXP development bundle under `dist/` and the CEP
bundle under `dist/cep`.
`npm run package` additionally creates these ignored release inputs:

- `packages/vidxp-premiere-cep.zxp`, signed and timestamped for Premiere
  23.0–25.5;
- `packages/vidxp-premiere-uxp.ccx` for Premiere 25.6 or newer.

The Desktop release workflow performs this packaging once and embeds both
artifacts in every Desktop installer. End users do not run these commands.

For UXP-only development, add `dist/manifest.json` to UXP Developer Tool or use
the checked-in VS Code attach configuration. Run `npm run dev` for Bolt UXP hot
reload. After `npm run package:uxp`, you can install or remove that development
CCX with Bolt's actions:

```bash
npm run ccx-install
npm run ccx-uninstall
```

CEP development uses an unsigned `dist/cep` build and therefore requires the
CEP debugging setup documented by Adobe. Neither developer path is an end-user
installation method.

Automated tests cover the shared VidXP client, library rules, React build, both
package builds, Desktop resource contract, and Desktop version routing. They do
not exercise Premiere, Creative Cloud, real media paths, a running VidXP
service, or model inference. Run the
[manual checklist](docs/MANUAL_TEST_CHECKLIST.md) for those boundaries.

## POC decisions

- Premiere file paths are passed to VidXP's local ingestion endpoint; source
  video bytes are not copied into extension storage.
- Bolt's optional webview, hybrid-plugin, and multi-host modes are disabled.
  The panel has one Premiere UXP context, and Bolt's hybrid helper targets
  Photoshop and InDesign rather than Premiere.
- `bolt-uxp-utils` is not used while the UXP package supports Premiere 25.6.
  The utility package requires Premiere 26.3 or newer, so the typed adapter
  calls Premiere's host API directly until the minimum supported version moves.
- CEP uses its Node HTTP client for loopback requests instead of broadening the
  API's browser CORS policy.
- Desktop owns runtime setup, service startup, extension detection,
  installation, removal, and user-visible installation status.
- Package host ranges do not overlap: CEP is 23.0–25.5 and UXP begins at 25.6.
- Bins expand recursively and source paths are deduplicated before submission.
- In-panel progress and completion notices are the primary workflow status.
- Timeline navigation and snippet insertion remain future adapter methods.
- Windows is the first release-validation target. Premiere UXP blocks plain
  loopback HTTP on macOS, so 25.6+ macOS support needs trusted local HTTPS.

The user setup is in
[`docs/integrations/premiere-pro.md`](../docs/integrations/premiere-pro.md).
