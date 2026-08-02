# Installation guide

Use this guide to choose one supported VidXP shape and install only what that
shape needs.

## Choose an installation

| Goal | Recommended installation | What runs |
|---|---|---|
| Native CLI indexing | `vidxp[local-worker]` | CLI plus a supervised local worker |
| Local agent integration | `vidxp[local-worker,mcp]` | Local worker and stdio MCP |
| Native browser UI | `vidxp[local-worker,frontend]` | CLI, local worker, Streamlit |
| Local application server | `vidxp[local-worker,server]` | Loopback HTTP API, remote MCP, local worker |
| Desktop app | Install the native package | Adopt a compatible local installation or create a private Desktop-managed runtime |
| Browser UI in Docker | Published `vidxp` image | One CPU worker/UI container |
| Public/self-hosted service | `compose.coolify.yaml` | API/MCP control plane, CPU worker, PostgreSQL, Chroma, tusd |
| Embed one capability | `dialogue`, `scene`, or `actor` extra | Python indexing/retrieval code |

Do not install the bare package and expect it to index video. Base `vidxp`
provides the lightweight command shell, configuration, and typed contracts.
Local model work requires a capability or worker extra.

## What your machine needs

| Installation | Install first | Managed by VidXP |
|---|---|---|
| CLI or MCP | [uv 0.12+](https://docs.astral.sh/uv/getting-started/installation/) | Python and the isolated VidXP environment |
| Desktop-managed target | A supported OS, internet access for first setup, FFmpeg, ffprobe, `libx264`, and `aac` | uv, Python, VidXP, and selected model files |
| Desktop with existing target | A compatible local `vidxp` executable and that installation's own media-runtime setup | Target discovery and launch coordination only |
| Docker | Docker Engine or Docker Desktop | Python, VidXP, and FFmpeg inside the image |

Native CLI and desktop processing require FFmpeg, ffprobe, `libx264`, and
`aac`. `vidxp init` checks them and offers the supported operating-system
package-manager command when something is missing. On Windows, Desktop can
show and run the WinGet command after consent when WinGet is available. On
macOS it can do the same with Homebrew; without Homebrew it provides Homebrew
or manual FFmpeg remediation. Linux displays the applicable APT, DNF, or
manual command without automating elevation. Docker already includes FFmpeg.

Supported native systems are Windows x86-64, Linux x86-64, and Apple Silicon
macOS 14 or newer. CPU is the supported runtime; GPU installation remains
deferred.

## Native local setup

uv installs VidXP as an isolated command-line tool, selects Python, and
resolves CPU PyTorch. Users do not need to create or activate a virtual
environment.

### 1. Install the profile you need

```bash
# Check uv
uv --version

# Install CLI and MCP
uv tool install --python 3.14 --torch-backend cpu "vidxp[local-worker,mcp]"
```

Replace the bracketed extras when you need another surface:

| Goal | Install this package profile |
|---|---|
| CLI indexing | `vidxp[local-worker]` |
| Browser UI | `vidxp[local-worker,frontend]` |
| Local stdio MCP | `vidxp[local-worker,mcp]` |
| Local API + remote MCP | `vidxp[local-worker,server]` |
| UI + API + both MCP transports | `vidxp[local-worker,frontend,server]` |

For example, add the browser interface with:

```bash
uv tool install --force --python 3.14 --torch-backend cpu \
  "vidxp[local-worker,mcp,frontend]"
```

`--torch-backend cpu` prevents an unintended CUDA dependency set on Linux and
keeps the supported runtime consistent across platforms. If `vidxp` is not
found after installation, run `uv tool update-shell` and reopen the terminal.

### 2. Initialize FFmpeg

```bash
vidxp init
```

`init` performs these steps:

1. resolves FFmpeg and ffprobe;
2. executes version checks;
3. verifies the `libx264` and `aac` encoders;
4. shows the exact supported package-manager command if anything is missing;
5. asks before running WinGet or Homebrew; and
6. stores the verified absolute paths in VidXP’s per-user configuration.

Linux prints an applicable APT, DNF, or pacman command for the user to run in a
system terminal. VidXP does not automate `sudo`.

For scripts and CI:

```bash
vidxp init --json \
  --ffmpeg /absolute/path/to/ffmpeg \
  --ffprobe /absolute/path/to/ffprobe
```

Redirected/noninteractive input never prompts or installs. `--yes` is an
explicit authorization to run the displayed supported package-manager command.

The lightweight one-off form is:

```bash
uvx vidxp init
```

Because the paths are stored in per-user configuration, a later permanent
installation can reuse initialization performed by `uvx`.

### 3. Prepare models

```bash
vidxp prepare
```

The command displays the models that are missing, their pinned download sizes,
the cache location, and the maximum additional disk use before asking for
confirmation.

| Capability | Models | Approximate download |
|---|---|---:|
| Dialogue | Qwen3 Embedding 0.6B + faster-whisper large-v3-turbo | 2.64 GiB |
| Scene | SigLIP2 base patch16-224 | 1.43 GiB |
| Actor | OpenCV Zoo YuNet + SFace | 37 MiB |

Prepare only what you plan to index:

```bash
vidxp prepare --modalities scene
vidxp prepare --modalities dialogue,actor
```

Noninteractive preparation requires explicit confirmation:

```bash
vidxp prepare --modalities scene --yes
```

Indexing, API jobs, and MCP tools never turn the first request into a hidden
model download.

#### Upgrading from the previous model stack

This release replaces:

- WhisperX `large-v2` and `all-MiniLM-L6-v2` with faster-whisper
  `large-v3-turbo` and Qwen3 Embedding 0.6B;
- OpenAI CLIP `ViT-B/32` with SigLIP2 base patch16-224; and
- `face_recognition`/dlib with OpenCV Zoo YuNet and SFace.

The resulting embeddings, actor thresholds, and provider manifests are not
compatible with indexes built by the earlier stack. Prepare the new models,
then re-index the registered videos you want in the active snapshot. Keep the
old repository until the replacement index has been validated.

### 4. Verify

```bash
vidxp doctor
```

`doctor` checks installed distributions, provider imports, FFmpeg, codecs, and
the selected pinned model artifacts. It never installs packages, changes the
operating system, constructs models, or downloads weights.

Limit the check when you prepared a subset:

```bash
vidxp doctor --modalities scene
vidxp doctor --modalities dialogue,actor
```

### 5. Start the selected surface

- CLI: `vidxp --help`
- Loopback browser UI: `vidxp ui`
- LAN-shared unauthenticated browser UI: `vidxp ui --share`
- Local HTTP API and MCP: `vidxp-api`
- LAN-shared authenticated HTTP API and MCP: `vidxp-api --share`
- Local stdio MCP: `vidxp-mcp`

The browser UI binds to loopback unless `--share` is present. In share mode,
VidXP gives Streamlit an explicit wildcard bind and Streamlit prints both the
Local and Network URLs. The UI has no authentication, so share it only on a
trusted network.
VidXP suppresses Streamlit's first-run email prompt and disables Streamlit
usage-statistics collection for this managed launch.

## First CLI index

```bash
vidxp media import samplevideo.mp4 --json
vidxp index create <media-id>
vidxp search scene "a yellow taxi on a city street"
```

- `media import` copies and validates the video in managed storage.
- `media list` rediscovers registered filenames and IDs.
- `index list` shows active searchable membership.
- Search/query without `--media-id` ranks across every indexed video.
- `--media-id <media-id>` restricts results to one video.

Index one capability or change scene cadence:

```bash
vidxp index create <media-id> \
  --modality scene \
  --scene-sample-fps 1
```

## Optional dependency extras

Extras are composable:

| Extra | Includes | Does not include |
|---|---|---|
| `storage` | Embedded Chroma and host monitoring | Model providers or UI |
| `dialogue` | Storage, transcription, dialogue embeddings | Scene/actor providers |
| `scene` | Storage, PyTorch, Transformers, OpenCV, Pillow | Dialogue/actor providers |
| `actor` | Storage, OpenCV, YuNet/SFace support | Dialogue/scene providers |
| `all` | Dialogue, scene, and actor | Grounded-query model client and UI |
| `local-worker` | `all` plus grounded-query client | Browser UI, MCP SDK, HTTP server |
| `frontend` | Streamlit | Worker providers |
| `mcp` | MCP SDK | Worker providers |
| `slm` | OpenAI-compatible local query-model client | A language model or model weights |
| `server` | FastAPI, remote MCP, PostgreSQL/control dependencies | Local providers |
| `server-worker` | Server storage client and every CPU provider | Public API process |
| `benchmarks` | Benchmark adapter dependencies | Local worker providers |
| `test` | Pytest and HTTP test client | Product runtime features |

The `server` extra is intentionally model-free. Use it with `local-worker` for
a loopback all-in-one API, or use the separate control and worker images for a
deployed server.

## Local MCP

After installing `local-worker,mcp`:

```bash
vidxp mcp-config
```

The command prints `mcpServers` JSON for Claude Desktop and other clients that
use that local-stdio format, with the resolved absolute `vidxp-mcp` executable
and default repository argument. It is not a universal MCP configuration.

Codex uses its own configuration. Either run:

```bash
codex mcp add vidxp -- vidxp-mcp --repository default
```

or add an `[mcp_servers.vidxp]` entry to `~/.codex/config.toml`. The ChatGPT
desktop app and Codex share that local MCP configuration. ChatGPT web does not
read this file or the generated JSON; connect it to a deployed HTTPS `/mcp`
endpoint through a custom app/connector instead.

```bash
vidxp-mcp --check --repository default
```

The self-check performs a real stdio handshake, discovers tools, calls the
read-only index-status tool, prints resolved data/index paths, and exits.

Useful alternatives:

| Command | Result |
|---|---|
| `vidxp mcp-config --repository <name>` | Client JSON for a named repository |
| `vidxp-mcp --print-config` | JSON without other output |
| `vidxp-mcp --help` | Options plus a copy/paste example |

## Local HTTP API

Install `local-worker,server`, prepare models, then run:

```bash
vidxp-api
```

The default is reachable only from the same machine. To deliberately share it
on the machine's detected LAN address, run `vidxp-api --share`. Share mode:

- generates and then reuses an app-owned bearer token;
- binds Uvicorn to the detected LAN address;
- configures the HTTP and MCP Host-header policies for that address; and
- prints the exact health URL, Streamable HTTP MCP URL, and bearer token.

The managed token is stored as `api-share-token` in VidXP's platform-native
configuration directory. Share mode uses plain HTTP and is intended for a
trusted local network; use the supported reverse-proxy deployment when TLS is
required.

The unauthenticated local default is deliberately loopback-only:

| Endpoint | Purpose |
|---|---|
| `http://127.0.0.1:32191/docs` | Interactive OpenAPI |
| `http://127.0.0.1:32191/openapi.json` | Machine-readable contract |
| `http://127.0.0.1:32191/health` | Process liveness |
| `http://127.0.0.1:32191/ready` | Aggregate runtime readiness |
| `http://127.0.0.1:32191/mcp` | Streamable HTTP MCP |

Native installs default to port `32191` to avoid the heavily reused development
port `8000`. Use `vidxp-api --port <port>` when a specific port is required.

The Streamable HTTP MCP endpoint includes `create_media_upload`. Its returned
capability link uses the actual listener host and port; opening `/` manually is
not an upload flow. Native mode serves the packaged Uppy page and receives bounded,
non-resumable multipart uploads directly in the API process. It requires no Docker,
PostgreSQL, Chroma server, tusd, or separately started helper. The session result
reports the effective per-file and aggregate limits, and `get_media_upload` follows
the durable import and automatic-indexing lifecycle through `indexed` and
`searchable=true`.

`vidxp-api --share` is different: it binds a bearer-protected, plain-HTTP LAN
listener but cannot safely synthesize an advertised browser handoff origin.
Consequently its MCP surface omits `create_media_upload` and
`get_media_upload` unless an explicit HTTPS
`VIDXP_UPLOAD_HANDOFF_PUBLIC_URL` is configured. The command reports that
omission instead of exposing a tool that would fail when called.

Local stdio exposes `ingest_local_media` instead. Pass one to ten paths that are
inside the configured import boundaries and poll `get_media_ingestion`; file bytes
do not cross MCP. Both ingestion tools default to the repository's advertised
capability set. Supply `modalities` to narrow it or
`index_after_import=false` for the advanced registration-only workflow.

Do not bind an unauthenticated API to a non-loopback address. Public
deployments require static bearer or OIDC authentication and should use the
supported server Compose topology. Hosted ChatGPT and Claude integrations
should use OIDC because those clients cannot be configured with VidXP's private
single-tenant static token. Set `VIDXP_HTTP_AUTH_MODE=oidc`, the issuer,
audience, JWKS URL, required scopes, and canonical HTTPS
`VIDXP_MCP_PUBLIC_URL`; VidXP publishes the MCP protected-resource metadata and
validates those access tokens.

## Desktop application

Download the Windows, Apple Silicon macOS, or Linux package from
[GitHub Releases](https://github.com/grayhatdevelopers/vidxp/releases).

Desktop opens its control panel first and asks which local target to use. It
does not install anything before that choice:

- **Use an existing installation** discovers compatible `vidxp` executables or
  lets you browse to one. Desktop validates the versioned probe and launch
  contracts, but the installation stays externally owned. Desktop never
  installs, repairs, updates, removes, or broadly stops it. If its browser
  surface is missing, enable the `frontend` extra with that installation's own
  package-management workflow before Desktop can open it.
- **Set up VidXP for me** creates a private Python and VidXP runtime owned by
  Desktop. Python and uv do not need to be installed separately. Capability
  code, the optional browser interface, model storage, and initial model
  preparation are selected before applying the draft.

A managed setup or update remains a draft until its candidate runtime passes
the Desktop probe and launch contracts. Activation then replaces the previous
managed target atomically; failed or cancelled work leaves the previous target
authoritative. For an unchanged ready runtime, **Prepare / verify models**
checks cached files and downloads only missing selected model material without
requiring a configuration change.

Starting Desktop, or starting it a second time, shows and focuses the control
panel without opening a browser. **Open VidXP** explicitly starts or reuses the
loopback browser service and opens one tab. Closing a configured window hides
it to the tray. Tray actions are **Manage VidXP**, **Open VidXP**, and **Quit
VidXP**. Quit stops the exact browser service Desktop launched; broad worker
shutdown is limited to a Desktop-owned runtime.

The NSIS, DMG, and AppImage packages do not bundle FFmpeg. Managed setup can
run WinGet on Windows or Homebrew on macOS only after native confirmation and
only when that package manager is available. Without Homebrew, macOS shows
installation remediation instead. Linux displays an APT, DNF, or manual
command and does not automate elevation. An adopted installation keeps
responsibility for its own FFmpeg setup.
Windows SmartScreen and macOS Gatekeeper may require explicit confirmation
until signing is added. See [Desktop application](docs/desktop.md) for runtime,
storage, and build details.

## Docker

The published local image contains the CPU worker, browser UI, Python, and
FFmpeg. Prepare models into a persistent volume, then start the app:

```bash
# Download selected models
docker run --rm -it \
  -v vidxp-data:/var/lib/vidxp \
  ghcr.io/grayhatdevelopers/vidxp:latest \
  vidxp prepare

# Start the browser app
docker run --rm --init \
  -p 8501:8501 \
  -v vidxp-data:/var/lib/vidxp \
  ghcr.io/grayhatdevelopers/vidxp:latest
```

Open `http://localhost:8501`. Pin a published version instead of `latest` for
a long-lived installation.

## Published container images

Every stable release publishes three Linux/amd64 images from the same validated
release commit:

| Image | Published tag | Purpose |
|---|---|---|
| Local | `<release>`, `<major>.<minor>`, `latest` | CPU worker + browser UI |
| Control | `<release>-control` | API, remote MCP, migrations, private upload hooks |
| Worker | `<release>-worker` | CPU model providers and server storage client |

Repository:

```text
ghcr.io/grayhatdevelopers/vidxp
```

The local image is used by `compose.yaml`. The control and worker images are
used by `compose.coolify.yaml`.

## Coolify and server Compose

The supported server topology contains:

| Control plane | Execution and storage | Ingestion |
|---|---|---|
| FastAPI + Streamable HTTP MCP | One CPU worker, PostgreSQL, Chroma | tusd + private hook service |

It is a single-node, single-repository deployment. It does not claim
multi-replica failover, hosted database substitution, or GPU execution.

Follow [Coolify deployment](docs/deployment/coolify.md) for:

- exact control/worker image variables;
- required secrets and public hostnames;
- proxy rules for MCP and resumable uploads;
- explicit worker model preparation through the authenticated API or MCP;
- readiness/migration gates;
- persistent volumes and backups; and
- the optional, explicitly prepared Ollama profile.

## Install from source

Source installation is for contributors and reproducible development, not the
normal public quick start:

```bash
git clone https://github.com/grayhatdevelopers/vidxp.git
cd vidxp
uv sync --frozen \
  --extra local-worker \
  --extra frontend \
  --extra mcp \
  --extra server
uv run --no-sync vidxp init
uv run --no-sync vidxp prepare
uv run --no-sync vidxp doctor
uv run --no-sync vidxp ui
```

The lock routes PyTorch through the official CPU index on Linux and Windows.

## Data and configuration locations

Local commands do not treat the current directory as their storage root.

| Platform | Data root | Configuration root |
|---|---|---|
| Windows | `%LOCALAPPDATA%\VidXP` | `%APPDATA%\VidXP` |
| macOS | `~/Library/Application Support/VidXP` | `~/Library/Application Support/VidXP` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/VidXP` | `${XDG_CONFIG_HOME:-~/.config}/VidXP` |

The data root contains:

```text
VidXP/
  repositories/
    default/
  models/
```

The desktop application uses this same root for repositories and its default
model cache. Its managed Python environments and active-runtime pointer remain
in the identifier-scoped private application-data directory. On Windows,
per-user program files are installed separately under
`%LOCALAPPDATA%\Programs\VidXP`.

Use an alternate root for one CLI invocation:

```bash
vidxp --data-dir /path/to/vidxp-data ui
```

Set `VIDXP_DATA_DIR` when the CLI, UI, local MCP, and local API should all use
the same alternate root. `VIDXP_INDEX_DIR` and `VIDXP_MODEL_CACHE` are advanced
single-location overrides. Docker uses its declared volumes instead.

## Troubleshooting

### `vidxp` is not found

- Run `uv tool update-shell`, restart the shell, and try again.
- Confirm the installation with `uv tool list`.

### FFmpeg or a codec is missing

```bash
vidxp init
```

Review the displayed package-manager command before approving it. Rerun
`vidxp doctor` afterward.

### Linux or Windows starts resolving NVIDIA packages

Reinstall the managed tool with the explicit CPU option:

```bash
uv tool install --force --python 3.14 --torch-backend cpu \
  "vidxp[local-worker,frontend]"
```

Do not use plain `pip install "vidxp[local-worker]"` on Linux unless you have
already installed and constrained the intended CPU PyTorch build.

### uv cannot hardlink from its cache

This warning means the cache and environment are on filesystems that cannot
hardlink to each other. Installation falls back to copying and remains valid.
Suppress the warning when that layout is intentional:

```bash
uv tool install --link-mode copy --python 3.14 --torch-backend cpu \
  "vidxp[local-worker,frontend]"
```

or set `UV_LINK_MODE=copy`.

### A model is missing or the worker is offline

```bash
vidxp prepare
vidxp doctor
```

For offline operation, prepare while network access is allowed, then set
`VIDXP_ALLOW_MODEL_DOWNLOADS=false`.

### Search says the index is not ready

- Check `vidxp index status`.
- Check `vidxp jobs list` and inspect the failed job.
- Retry indexing after fixing the reported dependency/model error.
- A failed or cancelled generation does not replace the previous active
  snapshot.

### Resetting local data

VidXP intentionally does not provide an implicit destructive reset. Inspect the
resolved paths first:

```bash
vidxp repositories show
```

Back up or remove only the specific per-user repository/model directories you
intend to discard.
