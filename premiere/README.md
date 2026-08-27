# VidXP Premiere Pro extension

This directory contains the React and TypeScript UXP panel for using VidXP
inside Premiere Pro. It is independent of the Uplift product; the local Uplift
repository was used only as a packaging and host-boundary reference.

## Architecture

```text
React workflow (`src/ui`)
├── typed VidXP HTTP client (`src/services/vidxp`)
└── Premiere UXP adapter (`src/premiere`)
    ├── project and sequence context
    ├── recursive bin and clip discovery
    └── Project panel selection and source-path resolution
```

Only the Premiere adapter imports the host-provided `premierepro` module. The
VidXP client uses HTTP contracts and has no host knowledge. The UI coordinates
selection, ingestion-session polling, and search-job polling without owning
either implementation.

Standard buttons, text fields, text areas, and checkboxes use the Spectrum UXP
widgets built into Premiere. A small typed React adapter owns native event
listeners, boolean attributes, and controlled values. The panel does not import
Spectrum Web Components or enable the SWC manifest flag. Application-specific
layout, media-tree rows, notices, and result cards remain local CSS. Those
custom surfaces follow Premiere's current theme through `document.theme`;
Premiere does not expose the Photoshop-style `--uxp-host-*` variables.

Capability names, descriptions, indexing support, and searchable roles come
from `GET /api/v1/capabilities`. The panel intentionally contains no mapping
for dialogue, sound, scene, action, or future capabilities.

## Development

```bash
npm ci
npm run check
```

The build writes the loadable UXP bundle to `dist/`. Add
`dist/manifest.json` to UXP Developer Tool; do not add the source manifest.
Vite keeps `premierepro`, `uxp`, and `os` external because Premiere provides
them at runtime.

Automated tests cover the VidXP client and pure media-library selection rules.
They do not exercise Premiere, UXP, the filesystem, a running VidXP service, or
model inference. Run the [manual checklist](docs/MANUAL_TEST_CHECKLIST.md) for
those boundaries.

## POC decisions

- The panel passes file paths returned by Premiere to VidXP's local ingestion
  endpoint. It does not request broad UXP filesystem access or copy video bytes.
- VidXP Desktop remains the setup and service-launch surface. The extension is
  separately packageable and does not embed or start a Python runtime.
- Bins expand recursively and source paths are deduplicated before submission.
- React remains at version 19. Built-in Spectrum event delivery and controlled
  values are release gates in the manual Premiere checklist.
- The bearer-token password input stays native because Adobe documents a macOS
  value-reading bug in the built-in Spectrum password field. The media scope
  also stays a native select, matching Adobe's current Premiere sample and
  avoiding unnecessary dropdown-index synchronization.
- In-panel progress and completion notices are the primary status UI.
- Timeline navigation and snippet insertion remain future adapter methods.
- Plain loopback HTTP is a Windows POC transport. Cross-platform local transport
  remains a packaging decision because Adobe restricts HTTP on macOS.

The corresponding user setup is in
[`docs/integrations/premiere-pro.md`](../docs/integrations/premiere-pro.md).
