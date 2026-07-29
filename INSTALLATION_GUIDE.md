# Installation guide

The main [README](README.md) contains the shortest path. This guide separates
the lightweight CLI from the native local-worker stack so CPU, CUDA, and model
dependencies are never installed accidentally.

## Supported local platforms

- Python 3.11 through 3.14
- Apple Silicon macOS 14 or newer
- Linux x86-64
- Windows x86-64
- FFmpeg and ffprobe on `PATH` for video/audio processing. VidXP wheels do not
  install or modify operating-system packages.

The actor capability uses OpenCV YuNet and SFace. It does not use `dlib`, CMake,
or a local C++ compiler.

CPU execution is the current default on every platform. CUDA is a later runtime
profile. MPS is not selected by `auto` until its capability parity gate is
complete.

## Install the lightweight CLI

Use pipx to expose `vidxp` on `PATH` while keeping its dependencies isolated:

```bash
pipx install vidxp
vidxp --version
```

This base install intentionally excludes Chroma, Torch, model providers,
Streamlit, and model weights. It is the correct foundation for a future remote
API client and for commands that do not execute local indexing.

## Install a published local worker

The `local-worker` extra contains Chroma and all current CPU capability
providers. The `frontend` extra adds Streamlit.

### macOS

PyPI publishes native macOS Torch wheels without the Linux CUDA dependency
stack:

```bash
pipx install "vidxp[local-worker,frontend]"
```

### Linux and Windows

Python package metadata cannot select a package index. Install the official CPU
Torch wheel into the pipx environment first, then install VidXP's local extras:

```bash
pipx install vidxp
pipx runpip vidxp install "torch==2.13.0+cpu" --index-url https://download.pytorch.org/whl/cpu
pipx runpip vidxp install "vidxp[local-worker,frontend]"
```

The final command accepts the already installed `2.13.0+cpu` build because it
satisfies VidXP's published `torch>=2.13,<3` requirement. Repeat the staged
Torch step after recreating the pipx environment.

Do not add a Torch wheel URL to VidXP dependency metadata. Direct URL
requirements are unnecessary here and can make published package metadata fail
repository validation.

## Install from source

The repository lock is the preferred development and reproducible local-worker
path. It routes Torch through an explicit CPU-only index on Linux and Windows,
uses PyPI's native wheel on macOS, and never uses `extra-index-url` mixing:

```bash
uv sync --frozen --extra local-worker --extra frontend
uv run vidxp doctor
```

Add benchmark-only dependencies when working on benchmark adapters:

```bash
uv sync --frozen --extra local-worker --extra benchmarks
```

For a non-repository virtual environment, uv can select the Torch backend
without embedding a direct URL in project metadata:

```bash
uv venv --python 3.14

# macOS/Linux
source .venv/bin/activate

# Windows PowerShell
.venv\Scripts\Activate.ps1

uv pip install --torch-backend cpu "vidxp[local-worker,frontend]"
```

## Local data location

Local installs do not use the directory where `vidxp` was launched as their
storage root. The CLI, browser UI, local MCP process, and locally launched
server use the operating system's per-user VidXP data directory:

| Platform | Default data root |
|---|---|
| Windows | `%LOCALAPPDATA%\VidXP` |
| macOS | `~/Library/Application Support/VidXP` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/VidXP` |

The default repository is under `repositories/default/` and prepared models are
under `models/` within that root. Named-repository configuration stays in the
operating system's standard user configuration directory, such as
`%APPDATA%\VidXP\repositories.json` on Windows. None of these paths are inside
the package environment or source checkout unless explicitly requested.

Use the global option when invoking the `vidxp` command:

```bash
vidxp --data-dir /path/to/vidxp-data doctor
vidxp --data-dir /path/to/vidxp-data ui
```

Set `VIDXP_DATA_DIR` instead when every local entry point, including
`vidxp-api` or `vidxp-mcp`, should use the same alternate root.
`VIDXP_INDEX_DIR` and `VIDXP_MODEL_CACHE` remain advanced per-location
overrides. Docker and Compose are separate deployment profiles and use their
explicitly configured volumes.

## Verify providers and model readiness without downloading

```bash
vidxp doctor
```

Restrict the check to selected capabilities:

```bash
vidxp doctor --modalities scene
vidxp doctor --modalities dialogue,actor
```

The doctor command checks installed distributions, imports each selected native
provider, and reports every pinned model artifact as cached or missing. It never
constructs models or downloads weights. Missing selected models make the command
exit unsuccessfully with the exact `vidxp prepare --modalities ...` remedy.

## Prepare pinned models

Model weights are cached separately from the Python environment:

| Capability | Provider/model |
|---|---|
| Dialogue embeddings | `Qwen/Qwen3-Embedding-0.6B` |
| Transcription | `dropbox-dash/faster-whisper-large-v3-turbo` |
| Scene search | `google/siglip2-base-patch16-224` |
| Actor detection | OpenCV Zoo YuNet |
| Actor recognition | OpenCV Zoo SFace |

Every model revision and weight checksum is pinned in its capability spec.
Explicitly prepare all selected models before indexing:

```bash
vidxp prepare
```

Indexing, API jobs, and MCP tools never download missing models implicitly.
Preparation is a durable job and reports download bytes plus model-loading
stages through CLI output or normal job polling. The CLI and UI show every
missing model's pinned size, the maximum additional cache space, and the cache
location before requiring confirmation. Non-interactive CLI preparation
requires `--yes`.

Prepare a subset when disk or network capacity is limited:

```bash
vidxp prepare --modalities scene
vidxp prepare --modalities dialogue,actor
vidxp prepare --modalities scene --yes  # non-interactive confirmation
```

Prepared models normally use the `models/` directory beneath the VidXP data
root described above. Set `VIDXP_MODEL_CACHE` only to override that one
location. Set `VIDXP_ALLOW_MODEL_DOWNLOADS=false` for an offline worker after
preparation.

## First indexing run

```bash
vidxp index create samplevideo.mp4
vidxp search scene "a yellow taxi on a city street"
```

Use fewer capabilities or adjust the scene sampling rate when appropriate:

```bash
vidxp index create samplevideo.mp4 \
  --modality scene \
  --scene-sample-fps 1
```

Indexes and manifests live under the configured repository root. Model identity,
revision, checksum, precision, license, and resolved runtime state are recorded
for reproducibility.

## Browser interface

With the `frontend` extra installed:

```bash
vidxp ui
```

The browser interface uses the same application service and capability
contracts as the CLI. It does not implement a second indexing/search layer.

## Run the current container

The CPU image contains the local worker and Streamlit frontend, but not model
weights. Compose persists indexes and the model cache:

```bash
docker compose pull
docker compose run --rm vidxp vidxp prepare
docker compose up
```

The interface is available at `http://localhost:8501`. Configure the image,
port, and CPU backend in `.env` when needed:

```dotenv
VIDXP_IMAGE=ghcr.io/grayhatdevelopers/vidxp:0.2.1
VIDXP_PORT=8501
VIDXP_DEVICE=cpu
```

The local image above is distinct from the prebuilt server topology. Stable
releases also publish `<release>-control` and `<release>-worker` images used by
the [Coolify deployment](docs/deployment/coolify.md).

## Common problems

### FFmpeg is not found

Run `vidxp doctor`; it checks both executables without downloading or changing
the machine. If either check fails, run `ffmpeg -version` and `ffprobe -version`
in the same terminal, install FFmpeg with the operating-system package manager
or add its executable directory to `PATH`, then rerun the doctor command.

### Linux pulls CUDA or NVIDIA packages

Do not use a plain published `vidxp[local-worker]` install on Linux. Recreate
the environment with the staged pipx commands above, use `uv pip
--torch-backend cpu`, or use the repository's frozen uv lock.

### A model cannot be used offline

Temporarily allow downloads, run `vidxp prepare` for the selected capability,
then restore `VIDXP_ALLOW_MODEL_DOWNLOADS=false`. The runtime verifies pinned
checksums before recording an artifact as resolved.

### Search says the index is not ready

Wait for indexing to finish. If the earlier process failed or was interrupted,
run the create command again. The current local worker resumes or rebuilds
according to its manifest and index state.
