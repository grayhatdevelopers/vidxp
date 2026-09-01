# Desktop development

This guide is for contributors working on the VidXP Desktop application. For
installation and everyday use, see the [installation guide](../INSTALLATION_GUIDE.md#desktop-app).

Desktop is a Tauri v2 control panel for a local VidXP installation. It helps a
user choose or create an installation, starts services on demand, and stops the
processes it owns. The CLI, HTTP, MCP, and Desktop surfaces all use the same
Python application contracts; Desktop must not become a second implementation
of VidXP behavior.

## What Desktop owns

Desktop calls the selected VidXP installation a **target**. It supports two
kinds:

| Target | Owned by | Desktop may do |
|---|---|---|
| Existing installation | The user or its original package manager | Check compatibility, save the selection, and start explicitly selected services |
| Desktop-managed installation | Desktop | Create, update, validate, activate, and remove features from the private runtime |

The distinction matters because Desktop has different responsibilities for
each target. Preserve these rules:

- Never select a discovered executable or install software without user
  confirmation.
- Check the exact executable the user selected before activating it. Do not
  substitute another executable found on `PATH`.
- Do not broadly stop an externally managed installation. Stop only the child
  processes Desktop started.
- Stage and validate a managed setup or update before making it active. A
  failed or cancelled operation must leave the previous target usable.
- Store repositories and model files in the normal VidXP data directory. This
  allows the CLI and Desktop to use the same product data. Store the managed
  Python runtime, activation state, and target profiles in Desktop's private
  application data instead.
- Keep model downloads explicit. Readiness checks, startup, indexing, and
  search must not silently download model files.
- Allow browser and app-integration services to accept connections only from
  the same computer by default. Sharing must be a separate action that shows
  the network address, authentication details, and exposure warning.

Desktop uses VidXP's existing DBOS worker for durable local jobs. It starts and
monitors that worker through the shared application service rather than
implementing another job system.

## Project map

The main Desktop files are:

| Path | Purpose |
|---|---|
| `desktop/src/` | React user interface and frontend tests |
| `desktop/src-tauri/src/` | Tauri commands, setup lifecycle, activation, and process supervision |
| `desktop/runtime-manifest.json` | Pinned Python and VidXP runtime versions |
| `desktop/sidecars.json` | Pinned `uv` sidecar versions and archive checksums |
| `desktop/capability-catalog.json` | Generated capability labels, installation extras, and model download plans |
| `desktop/model-cache-catalog.json` | Generated model-cache recognition catalog |
| `desktop/scripts/` | Sidecar, model-catalog, notice, branding, and package scripts |
| `desktop/THIRD_PARTY_NOTICES.txt` | Generated notices shipped with the installers |

Repositories and models live in the shared VidXP data directory. The managed
runtime and activation state live in Desktop's private application directory.
Both locations vary by operating system, so use the existing Python or Tauri
path helpers instead of hard-coding them.

## Build locally

From the repository root, install the locked frontend dependencies and fetch
the sidecar for your platform.

Windows:

```powershell
npm --prefix desktop ci
npm --prefix desktop run sidecar:windows
npm --prefix desktop run desktop:dev
```

macOS Apple Silicon or Linux x86-64:

```bash
npm --prefix desktop ci
npm --prefix desktop run sidecar:unix
npm --prefix desktop run desktop:dev
```

Build the native package with:

```bash
npm --prefix desktop run desktop:build
```

The sidecar script downloads the pinned official `uv` archive and checks its
SHA-256 digest against `desktop/sidecars.json`. It then places the executable
where Tauri expects it for that build target. Generated sidecars and build
output are ignored by Git.

Local builds are unsigned. The selected package is NSIS on Windows, DMG on
macOS, and AppImage on Linux.

## Validate a change

Run the smallest checks that cover the files you changed. Before running the
complete Desktop checks, install the frontend dependencies and prepare the
locked Rust dependency information used by notice generation:

```bash
npm --prefix desktop ci
cargo install cargo-about --version 0.9.1 --locked --features cli
cargo fetch --manifest-path desktop/src-tauri/Cargo.toml --locked
```

Run the generated-file checks, frontend suite, Python package build, sidecar
check, and Rust tests:

```bash
npm --prefix desktop run model-catalog:check
npm --prefix desktop run notices:check
npm --prefix desktop run check
python -m build
npm --prefix desktop run sidecar:unix
cargo test --release --locked --manifest-path desktop/src-tauri/Cargo.toml
```

Use `npm --prefix desktop run sidecar:windows` instead of `sidecar:unix` on
Windows. Report the exact commands you ran and any platform package you could
not build or inspect.

Three checked-in files must stay synchronized with their source contracts:

- After changing capability labels, descriptions, extras, or model contracts,
  run
  `npm --prefix desktop run model-catalog:write`, review the diff, and run the
  corresponding `:check` command. This updates both Desktop catalogs from the
  canonical capability registry.
- After changing a production dependency or license, run
  `npm --prefix desktop run notices:write`, review the inventory, and run the
  corresponding `:check` command.

Do not edit generated catalog entries or notices by hand.

## Setup behavior to preserve

Setup follows the same broad sequence for either target: the user chooses an
installation, Desktop checks its executable, and only a compatible result can
become active. Creating or updating a managed target adds a staging step before
activation.

### Existing installation

When a user selects an existing installation:

1. Discover candidates on `PATH` or accept a file chosen by the user.
2. Run that executable's read-only `desktop-probe --json` command. The report
   describes the installation, supported features, data locations, and the
   contract versions Desktop uses to communicate with it.
3. If the report is incompatible, say whether VidXP or Desktop must be updated
   and leave the current active target unchanged.
4. Save only non-secret target metadata. Do not store remote credentials or
   bearer tokens in the profile.
5. Leave package and broad process ownership with the original installation.

Desktop may update an isolated `uv tool` installation only after the user
confirms a feature change. It recreates that tool environment with compatible
VidXP and Python versions and then checks it again. Desktop must leave every
other external environment with its original package manager.

### Desktop-managed installation

When Desktop creates or updates its own installation:

1. Check the host media tools before creating Python or installing VidXP.
2. Install the versions pinned in `runtime-manifest.json` into a staged private
   runtime.
3. Run `vidxp init` so the new runtime records the verified FFmpeg and ffprobe
   paths.
4. Install only the selected features and prepare only the selected models.
5. Run `vidxp doctor` and the launch checks.
6. Make the staged runtime active only after every check passes. Until that
   point, the previous runtime remains the working target.

The user-facing feature choices map to package extras as follows:

| Desktop choice | Package extra | Result |
|---|---|---|
| Local video processing | `local-worker` | Built-in search features and the local worker |
| Browser interface | `frontend` | Local browser interface |
| AI assistant integration | `mcp` | MCP server launched as a local process |
| App integration service | `server` | Local API and Streamable HTTP MCP |

These package names are implementation details and should not replace the
product labels in the interface.

The capability registry owns capability labels, descriptions, installation
extras, and model specifications. Desktop embeds a generated capability
catalog because it must show setup choices before a VidXP runtime exists. The
build merges that generated catalog with Desktop-owned surface and runtime
metadata; React must not maintain a parallel capability list or storage table.

The installer does not bundle FFmpeg. If it is missing, setup may offer the
supported WinGet command on Windows or Homebrew command on macOS, but it must
wait for user confirmation before running either one. On Linux, setup shows an
APT, DNF, or manual command and leaves elevation to the user. Existing
installations remain responsible for their own media setup.

Local grounded answers do not justify installing another desktop application.
Desktop reuses a healthy Ollama service without taking ownership, then checks
for an existing executable. When neither is available on Windows x86-64 or
macOS Apple Silicon, it downloads the pinned headless archive declared in
`runtime-manifest.json`, verifies its byte count and SHA-256 digest, extracts it
into Desktop's private application data, and starts `ollama serve` through the
shared process supervisor. Downloads are cancellable, incomplete archives and
staging directories are removed, and only a completely extracted version is
activated. Linux and unsupported architectures require an external Ollama
installation. Desktop never invokes an Ollama desktop-app installer.

Starting Desktop shows the control panel without opening a browser. **Open
VidXP** starts or reuses the browser service and opens one tab. Closing the
configured window hides it in the system tray. **Quit VidXP** stops only the
services and managed worker started by that Desktop instance.

Codex integration must use the selected runtime's absolute `vidxp-mcp` command
and the supported Codex CLI configuration commands. Keep the end-user steps in
the [AI assistant integration guide](integrations/openai-plugin.md), not in
this implementation guide.

## Packaging and releases

VidXP currently publishes:

| Platform | Package |
|---|---|
| Windows x86-64 | Per-user NSIS installer |
| macOS Apple Silicon | DMG |
| Linux x86-64 | AppImage |

The release workflow builds all three Desktop packages from the stamped
release commit and attaches them to one GitHub release. Before those builds it
also creates the signed CEP `.zxp` and UXP `.ccx` Premiere packages. Every
Desktop installer embeds both packages as resources, so installation happens
on the user's computer without source code or build tooling.

Desktop detects standard Premiere installations, assigns versions 23.0–25.5
to CEP and 25.6 or newer to UXP, and calls Adobe's Unified Plugin Installer
Agent. If Adobe requires an interactive confirmation, Desktop opens the
already-built package with Creative Cloud. The package host ranges are
non-overlapping, so both may remain installed on a workstation with multiple
Premiere generations.

Beta and stable macOS DMGs are signed with a Developer ID certificate and the
hardened runtime. The workflow then notarizes, staples, and verifies each DMG
before publication. The Windows installer is currently unsigned, so
SmartScreen may require user confirmation.

Each release candidate embeds the VidXP wheel built and tested for that same
candidate. Managed setup installs the selected extras under the dependency
constraints bundled with Desktop. It never uses TestPyPI; that service is
reserved for nightly Python-package validation.

Every installer includes the root MIT license and generated third-party
notices. Do not bundle FFmpeg until the exact binary provenance, enabled
codecs, and redistribution licenses are recorded and approved.

Automatic Desktop updates, richer tray controls, in-app playback, CUDA
installers, and fully offline installers are not implemented yet.
