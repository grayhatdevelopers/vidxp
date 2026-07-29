# Desktop application

The desktop application is a thin Tauri v2 installer and process supervisor. It
does not contain a second VidXP implementation:

- the selected capability extras are installed from the exact configured
  package release;
- the existing Streamlit adapter remains the local human interface;
- the repository, model cache, managed Python, and package environments use
  platform application-data directories;
- the existing DBOS worker remains the durable execution boundary; and
- closing the desktop process stops both Streamlit and the repository worker.

The first release is an online bootstrap. It bundles `uv`, installs the exact
Python and VidXP versions in `desktop/runtime-manifest.json`, and activates a new
runtime only after `vidxp doctor` passes. A failed setup never replaces the
previous active runtime.

## Build locally

From `desktop/`:

```powershell
npm install
npm run sidecar:windows
npm run desktop:dev
```

On macOS Apple Silicon or Linux x86-64:

```bash
npm install
npm run sidecar:unix
npm run desktop:dev
```

The sidecar scripts download the pinned official `uv` release, verify its
checked-in SHA-256 archive digest from `desktop/sidecars.json`, and place the
target-suffixed binary where Tauri expects it. The Rust build also executes the
target sidecar and rejects a version mismatch. Generated binaries and build
outputs are ignored by Git.

## Installation behavior

Users select dialogue, scene, and actor capabilities independently. The
installer always adds the `frontend` extra and uses a single sorted extra set.
For the current beta, it first acquires only the exact VidXP package from
TestPyPI with dependency resolution disabled. It then resolves that installed
package's selected extras from production PyPI. This prevents TestPyPI from
becoming a competing source for transitive dependencies. The acquisition index
is derived from the stamped package version: prereleases use TestPyPI and stable
versions use production PyPI.
Windows and Linux resolve CPU-only PyTorch wheels using uv's
`--torch-backend cpu`; macOS uses native PyPI wheels. The custom PyTorch index
is therefore a resolver input and is not embedded as a package URL, avoiding
the prior package-publication failure mode.

Optional model preparation invokes the shared `vidxp prepare` command for only
the selected modalities. Setup subprocesses are owned by the Tauri supervisor;
closing the app cancels the active process and stops a preparation worker before
exit. No model is bundled in the installer.

`vidxp doctor` validates FFmpeg and ffprobe as system dependencies before
activating the staged runtime. The Python package and desktop bootstrap do not
install OS packages. Target binaries will not be bundled until the exact build
provenance, enabled codecs, and redistribution licenses are recorded. This is
an explicit packaging gate, not a silent download from an unaudited third
party.

## Release targets

The initial target matrix is:

- Windows x86-64: per-user NSIS installer;
- macOS Apple Silicon: signed and notarized DMG; and
- Linux x86-64: AppImage built on the oldest supported glibc baseline.

Updater integration, tray behavior, in-app playback, CUDA installers, and true
offline installers remain deferred until signing and binary provenance are in
place.
