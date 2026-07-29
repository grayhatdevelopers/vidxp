# Installation guide

Use this guide to choose one supported VidXP shape and install only what that
shape needs.

## Choose an installation

| Goal | Recommended installation | What runs |
|---|---|---|
| Try the browser UI locally | `compose.yaml` | One CPU worker/UI container |
| Native CLI indexing | `vidxp[local-worker]` | CLI plus a supervised local worker |
| Native browser UI | `vidxp[local-worker,frontend]` | CLI, local worker, Streamlit |
| Local agent integration | `vidxp[local-worker,mcp]` | Local worker and stdio MCP |
| Local application server | `vidxp[local-worker,server]` | Loopback HTTP API, remote MCP, local worker |
| Desktop preview | Build the Tauri app | Guided app-owned Python/worker/UI runtime |
| Public/self-hosted service | `compose.coolify.yaml` | API/MCP control plane, CPU worker, PostgreSQL, Chroma, tusd |
| Embed one capability | `dialogue`, `scene`, or `actor` extra | Python indexing/retrieval code |

Do not install the bare package and expect it to index video. Base `vidxp`
provides the lightweight command shell, configuration, and typed contracts.
Local model work requires a capability or worker extra.

## Requirements

- Python 3.11 through 3.14.
- Windows x86-64, Linux x86-64, or Apple Silicon macOS 14 and newer.
- FFmpeg, ffprobe, `libx264`, and `aac` for media operations.
- CPU execution. **GPU remains explicitly deferred and is not a supported
  installation profile.**

Python wheels never install operating-system packages. `vidxp init` is the
explicit, guided FFmpeg setup command; `vidxp doctor` is always read-only.
Native installation uses
[uv 0.12 or newer](https://docs.astral.sh/uv/getting-started/installation/).
Docker users do not need a host Python or uv installation.

## Fastest local setup: Docker

The local image contains the CPU worker, browser UI, and FFmpeg. Model weights
remain an explicit first-run download.

```bash
git clone https://github.com/grayhatdevelopers/vidxp.git
cd vidxp
docker compose pull
docker compose run --rm vidxp vidxp prepare
docker compose up
```

Open `http://localhost:8501`.

| Setting | Default | Purpose |
|---|---|---|
| `VIDXP_IMAGE` | `ghcr.io/grayhatdevelopers/vidxp:latest` | Local image tag or digest |
| `VIDXP_PORT` | `8501` | Host port for the browser UI |
| `VIDXP_DEVICE` | `cpu` | Runtime device; CPU is the supported value |
| `vidxp-data` volume | Managed by Compose | Media, indexes, jobs, artifacts, and models |

Stop the app with `Ctrl+C` or:

```bash
docker compose down
```

`docker compose down` keeps the named volume. Add `--volumes` only when you
intentionally want to remove all persisted VidXP data.

## Native local setup

uv installs VidXP as an isolated command-line tool, selects Python, and
resolves CPU PyTorch. Users do not need to create or activate a virtual
environment.

### 1. Install the profile you need

```bash
uv --version
uv tool install --python 3.14 --torch-backend cpu "vidxp[local-worker,frontend]"
```

The command above installs the complete local browser app. Replace its bracketed
extras when you need another surface:

| Goal | Install this package profile |
|---|---|
| CLI indexing | `vidxp[local-worker]` |
| Browser UI | `vidxp[local-worker,frontend]` |
| Local stdio MCP | `vidxp[local-worker,mcp]` |
| Local API + remote MCP | `vidxp[local-worker,server]` |
| UI + API + both MCP transports | `vidxp[local-worker,frontend,server]` |

For example, install the local MCP profile with:

```bash
uv tool install --python 3.14 --torch-backend cpu "vidxp[local-worker,mcp]"
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
- Browser UI: `vidxp ui`
- Local HTTP API and remote MCP: `vidxp-api`
- Local stdio MCP: `vidxp-mcp`

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

The command prints a complete, import-ready `mcpServers` JSON object with the
resolved absolute `vidxp-mcp` executable and default repository argument.

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

The unauthenticated local default is deliberately loopback-only:

| Endpoint | Purpose |
|---|---|
| `http://127.0.0.1:8000/docs` | Interactive OpenAPI |
| `http://127.0.0.1:8000/openapi.json` | Machine-readable contract |
| `http://127.0.0.1:8000/health` | Process liveness |
| `http://127.0.0.1:8000/ready` | Aggregate runtime readiness |
| `http://127.0.0.1:8000/mcp` | Streamable HTTP MCP |

Do not bind an unauthenticated API to a non-loopback address. Public
deployments require static bearer or OIDC authentication and should use the
supported server Compose topology.

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

## Desktop preview

The Tauri desktop shell performs setup in this order:

```text
FFmpeg preflight
→ explicit package-manager consent when missing
→ app-owned Python and VidXP installation
→ optional model preparation
→ full doctor
→ atomic runtime activation
```

The NSIS/DMG/AppImage packages do not install FFmpeg themselves. Desktop
packaging is currently a source-built preview; signed installer publication is
still under release validation. Build instructions are in
[Desktop application](docs/desktop.md).

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
