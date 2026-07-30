# Desktop application

The desktop application is a Tauri v2 launcher, first-run configuration
surface, and process supervisor. The operating-system package installs the
application; the application then provisions its local processing runtime. It
does not contain a second VidXP implementation:

- the selected capability extras are installed from the exact configured
  package release;
- the Streamlit browser interface is an optional installation surface;
- repositories and models use the same platform VidXP data directory as the
  CLI, while managed Python and package environments use private desktop
  application-data directories;
- the existing DBOS worker remains the durable execution boundary; and
- closing the desktop process stops both Streamlit and the repository worker.

The desktop separates shared product data from its private implementation
state. The shared root is the same operating-system VidXP data directory used
by the CLI:

```text
VidXP/
  repositories/
    default/
  models/                    # default; a user-selected directory is also supported
```

The identifier-scoped private desktop directory contains only the managed
runtime and desktop state:

```text
dev.grayhat.vidxp/
  runtimes/
  python/
  active-runtime.json
```

On Windows the per-user NSIS package installs program files under
`%LOCALAPPDATA%\Programs\VidXP`, keeping them separate from both directories.
The desktop's uv download cache uses the operating system-provided app-cache
directory. Docker and Compose storage remains explicitly volume-backed and does
not inherit this desktop layout.

The application bundles `uv` and performs a system-media preflight before
creating Python or downloading VidXP. If FFmpeg is missing on Windows or
macOS, the application uses a native operating-system dialog to show the exact
approved package-manager command and obtain consent before running it. Linux
shows the applicable terminal command without trying to automate elevation.
The application verifies ffmpeg, ffprobe,
`libx264`, and `aac`, then installs the exact Python and VidXP versions in
`desktop/runtime-manifest.json`, persists the verified absolute executable
paths through `vidxp init`, runs the full `vidxp doctor`, and only then activates
the new runtime. A failed or cancelled setup never replaces the previous active
runtime.

## Build locally

From `desktop/`:

```powershell
npm ci
npm run sidecar:windows
npm run desktop:dev
npm run desktop:build
```

On macOS Apple Silicon or Linux x86-64:

```bash
npm ci
npm run sidecar:unix
npm run desktop:dev
npm run desktop:build
```

The sidecar scripts download the pinned official `uv` release, verify its
checked-in SHA-256 archive digest from `desktop/sidecars.json`, and place the
target-suffixed binary where Tauri expects it. The Rust build also executes the
target sidecar and rejects a version mismatch. Generated binaries and build
outputs are ignored by Git. `desktop:build` selects NSIS on Windows, DMG on
macOS, and AppImage on Linux, disables signing, and requires the checked-in
Cargo lock to remain unchanged.

## First-run configuration

Users select dialogue, scene, and actor capabilities independently. Interfaces
are selected separately: the browser interface adds the `frontend` extra only
when selected. Model preparation can be deferred, and a native folder picker
can select a model-cache directory before any model is downloaded.
For the current beta, it first acquires only the exact VidXP package from
TestPyPI with dependency resolution disabled. It then resolves that installed
package's selected extras from production PyPI. This prevents TestPyPI from
becoming a competing source for transitive dependencies. The acquisition index
is derived from the stamped package version: prereleases use TestPyPI and stable
versions use production PyPI.
Windows and Linux resolve CPU-only PyTorch wheels using uv's
`--torch-backend cpu`; macOS uses native PyPI wheels. The custom PyTorch index
is therefore a resolver input and is not embedded as a package URL, avoiding
the prior package-publication failure mode. Every selected profile is also
constrained by `desktop/runtime-constraints.txt`, exported from the repository
lock for the complete local-worker and frontend dependency set. Capability
selection controls which packages are installed; the constraints prevent those
packages from drifting independently after the desktop binary is published.

Optional model preparation invokes the shared `vidxp prepare` command for only
the selected modalities. Setup subprocesses are owned by the Tauri supervisor;
closing the app cancels the active process and stops a preparation worker before
exit. No model is bundled in the installer.

After a runtime is configured, the Tauri supervisor starts hidden in the system
tray. A browser-enabled profile opens the local interface in the operating
system's default browser. **Open VidXP** reuses the already-running interface;
**Quit VidXP** runs the full supervised shutdown for the interface and
repository worker. Closing the configuration/status window hides it to the tray
instead of terminating a configured runtime.

The native NSIS, DMG, and AppImage packages themselves never install FFmpeg or
run a package manager. That consented action belongs to first-run configuration,
where errors and retries are visible. `vidxp doctor` remains a read-only validation
gate. Target FFmpeg binaries will not be bundled until their exact build
provenance, enabled codecs, and redistribution licenses are recorded. This is
an explicit packaging gate, not a silent download from an unaudited third
party.

## Release targets

The initial target matrix is:

- Windows x86-64: per-user NSIS installer;
- macOS Apple Silicon: DMG; and
- Linux x86-64: AppImage.

The release workflow builds all three from the stamped release commit and
attaches them to the matching GitHub release. Initial packages are unsigned;
Windows SmartScreen and macOS Gatekeeper may therefore require explicit user
confirmation. Signing and notarization improve that experience but do not
block publication.

The Tauri shell itself uses the operating system webview. Selecting no browser
surface omits Streamlit and its Python dependencies, but does not turn the
Tauri executable into a non-webview application. Users who require no webview
at all should install a CLI/MCP package profile instead of the desktop package.

Updater integration, richer tray controls, in-app playback, CUDA installers,
and true offline installers remain deferred.
