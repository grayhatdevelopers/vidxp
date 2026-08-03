# Desktop application

The desktop application is a Tauri v2 launcher, target-selection surface, and
process supervisor. On first launch it asks which local VidXP target to use
before offering any installation. It does not contain a second VidXP
implementation:

- an existing compatible `vidxp` executable can be adopted without downloading
  Python, VidXP, FFmpeg, models, or another environment;
- executable discovery never selects a candidate without user confirmation;
- adopted installations remain externally owned and cannot be installed,
  repaired, removed, or broadly stopped by the desktop;
- the selected capability extras are installed from the exact configured
  package release only after the managed target is explicitly chosen;
- the Streamlit browser interface is an optional installation surface;
- managed repositories and models use the same platform VidXP data directory
  as the CLI; adopted targets retain their reported roots, while managed Python
  and package environments use private desktop application-data directories;
- the existing DBOS worker remains the durable execution boundary; and
- closing the desktop process stops the exact interface process it launched,
  while broad worker shutdown remains limited to desktop-owned runtimes.

The desktop separates shared product data from its private implementation
state. The shared root is the same operating-system VidXP data directory used
by the CLI:

```text
VidXP/
  repositories/
    default/
  models/                    # default; a user-selected directory is also supported
```

The identifier-scoped private desktop directory contains the managed runtime
and its activation journal/pointer:

```text
dev.grayhat.vidxp/
  runtimes/
  python/
  active-runtime.json
  activation-journal.json    # present only during recoverable activation
```

Target profiles are non-secret Tauri Store data. Their platform-specific store
location is resolved by the Tauri Store plugin and must not be assumed to be
the same directory as `active-runtime.json`.

On Windows the per-user NSIS package installs program files under
`%LOCALAPPDATA%\Programs\VidXP`, keeping them separate from both directories.
The desktop's uv download cache uses the operating system-provided app-cache
directory. Docker and Compose storage remains explicitly volume-backed and does
not inherit this desktop layout.

The application bundles `uv` and performs a system-media preflight before
creating Python or downloading VidXP. If FFmpeg is missing, Windows can show
and run the approved WinGet command after native confirmation when WinGet is
available. macOS can do the same with Homebrew; without Homebrew it provides
Homebrew or manual FFmpeg remediation instead. Linux shows the applicable APT,
DNF, or manual terminal command without trying to automate elevation.
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

The target-first screen offers two paths:

- **Existing local installation** discovers `vidxp` executables on `PATH` or
  accepts an executable selected with the native file picker. The desktop shows
  its canonical path and runs `vidxp desktop-probe --json` before activation.
  The probe is side-effect free and reports its raw launcher identity, package
  version, probe and launch contract metadata, Python identity, local data
  roots, and browser-interface availability. Desktop compares that report with
  the exact executable the user selected and decides compatibility. Reopening
  Desktop restores the selected profile immediately and rechecks it once in the
  background.
- **Desktop-managed runtime** reveals the existing capability, model, and media
  setup only after explicit confirmation. The staged installation and activation
  boundary is unchanged.

Target profiles use a versioned desktop-private schema. Profile content and the
selected profile identity are stored separately. No credentials or remote tokens
are stored; remote targets are intentionally outside this release.

Users select dialogue, scene, and actor capabilities independently. Interfaces
are selected separately: the browser interface adds the `frontend` extra only
when selected. Model preparation can be deferred, and a native folder picker
can select a model-cache directory before any model is downloaded.
The managed runtime acquires the exact VidXP package with dependency resolution
disabled, then resolves that package's selected extras. Beta and stable desktop
releases use production PyPI for both steps, so a pinned prerelease and its
normal dependencies come from one authoritative index. TestPyPI is used only
for package-only nightly validation and is never a desktop runtime source.
Windows and Linux resolve CPU-only PyTorch wheels using uv's
`--torch-backend cpu`; macOS uses native PyPI wheels. The custom PyTorch index
is therefore a resolver input and is not embedded as a package URL, avoiding
the prior package-publication failure mode. Every selected profile is also
constrained by `desktop/runtime-constraints.txt`, exported from the repository
lock for the complete local-worker and frontend dependency set. Capability
selection controls which packages are installed; the constraints prevent those
packages from drifting independently after the desktop binary is published.

FFmpeg and ffprobe are host prerequisites. When WinGet is available, Windows
can show and run the supported FFmpeg install command after consent. When
Homebrew is available, macOS can similarly offer `brew install ffmpeg`; without
Homebrew it instructs the user to install [Homebrew](https://brew.sh/), install
FFmpeg manually, or otherwise make FFmpeg and ffprobe available on `PATH`.
Linux displays the detected APT or DNF command, with a manual command as the
fallback, and does not automate elevation. Adopted installations remain
responsible for their own media-runtime setup.

Optional model preparation invokes the shared `vidxp prepare` command for only
the selected modalities. A ready managed runtime also exposes a separate
**Prepare / verify models** action, so verification does not require a fake
configuration change. Setup, probe, worker-stop, and browser-service children
share one process ownership policy: null stdin, bounded captured output where
applicable, cancellation and timeouts, and whole-tree termination/reaping.
Closing the app cancels an active managed operation and stops the exact browser
service it owns. No model is bundled in the installer.

Desktop startup and a second-instance activation show and focus the control
panel; neither action opens the browser. For a browser-enabled profile, **Open
VidXP** explicitly starts or reuses the supervised loopback service and opens
one browser tab. Closing a configured control panel hides it to the tray.
**Manage VidXP** shows the panel, **Open VidXP** performs the separate browser
action, and **Quit VidXP** runs supervised shutdown for the interface and any
Desktop-owned repository worker.

## Implementation dependencies

Tauri Shell commands are converted to standard commands and passed through one
shared runner. `process-wrap` 9.1.0 (MIT/Apache-2.0) supplies Windows Job Object
and POSIX process-group ownership while preserving Tauri sidecar resolution;
the runner's deadline-aware monitor supplies bounded waits, including while
descendants retain output pipes. The runner hides Windows consoles, closes
stdin, bounds captured output, applies operation-specific
timeouts and cancellation, and kills and reaps the owned process tree on every
post-spawn failure path. The loopback UI uses the same ownership abstraction as
a long-lived service.

React's reducer plus the local async-action helper is sufficient for the finite
setup lifecycle, so no state framework was added. Generated IPC was evaluated:
`tauri-specta` would require replacing command macros and build integration
while the transition service is still changing, and `ts-rs` generates DTOs but
not the command calls. The current adapter is therefore limited to exact,
consumed commands and one presentation normalizer.

Distributable notices are generated from locked production graphs with
`cargo-about` 0.9.1 (MIT/Apache-2.0) under `--locked --frozen` and
`license-checker-rseidelsohn` 4.4.2 (BSD-3-Clause). Build-only and development
Rust crates are excluded; frontend development packages are excluded with the
tool's production graph. The generated inventory includes the bundled uv
0.12.0 executable and its upstream license, and the bundle separately contains
VidXP's root MIT `LICENSE`. Both `THIRD_PARTY_NOTICES.txt` and the project
license are packaged in NSIS, DMG, and AppImage resources.

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
