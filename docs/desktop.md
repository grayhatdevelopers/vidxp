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

The desktop uses the operating system-provided app-local-data root rather than
the installation directory or current working directory. Its relevant layout
matches other local installs:

```text
app-local-data/
  repositories/
    default/
  models/
  runtimes/
  python/
  active-runtime.json
```

The last three entries are desktop bootstrap state. The desktop's uv download
cache uses the operating system-provided app-cache directory. Docker and
Compose storage remains explicitly volume-backed and does not inherit this
desktop layout.

The first release is an online bootstrap. It bundles `uv` and performs a
system-media preflight before creating Python or downloading VidXP. If FFmpeg
is missing on Windows or macOS, setup shows the exact approved package-manager
command and asks before running it. Linux shows the applicable terminal command
without trying to automate elevation. Setup verifies ffmpeg, ffprobe,
`libx264`, and `aac`, then installs the exact Python and VidXP versions in
`desktop/runtime-manifest.json`, persists the verified absolute executable
paths through `vidxp init`, runs the full `vidxp doctor`, and only then activates
the new runtime. A failed or cancelled setup never replaces the previous active
runtime.

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

The native NSIS, DMG, and AppImage packages themselves never install FFmpeg or
run a package manager. That consented action belongs to first-run setup, where
errors and retries are visible. `vidxp doctor` remains a read-only validation
gate. Target FFmpeg binaries will not be bundled until their exact build
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
