# Installation guide

The main [README](README.md) contains the shortest path. This guide separates
the lightweight CLI from the native local-worker stack so CPU, CUDA, and model
dependencies are never installed accidentally.

## Supported local platforms

- Python 3.11 through 3.14
- Apple Silicon macOS 14 or newer
- Linux x86-64
- Windows x86-64
- FFmpeg and ffprobe on `PATH` for video/audio processing

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

## Verify providers without downloading models

```bash
vidxp doctor
```

Restrict the check to selected capabilities:

```bash
vidxp doctor --modalities scene
vidxp doctor --modalities dialogue,actor
```

The doctor command checks installed distributions and imports each selected
native provider without constructing models or downloading model weights.

## Prepare pinned models

Model weights are cached separately from the Python environment:

| Capability | Provider/model |
|---|---|
| Dialogue embeddings | `Qwen/Qwen3-Embedding-0.6B` |
| Transcription | `mobiuslabsgmbh/faster-whisper-large-v3-turbo` |
| Scene search | `google/siglip2-base-patch16-224` |
| Actor detection | OpenCV Zoo YuNet |
| Actor recognition | OpenCV Zoo SFace |

Every model revision and weight checksum is pinned in its capability spec.
Prepare all selected models before indexing:

```bash
vidxp prepare
```

Prepare a subset when disk or network capacity is limited:

```bash
vidxp prepare --modalities scene
vidxp prepare --modalities dialogue,actor
```

Set `VIDXP_MODEL_CACHE` to choose the cache directory. Set
`VIDXP_ALLOW_MODEL_DOWNLOADS=false` for an offline worker after preparation.

## First indexing run

```bash
vidxp index create samplevideo.mp4
vidxp search scene "a yellow taxi on a city street"
```

Use fewer capabilities or a larger visual stride when appropriate:

```bash
vidxp index create samplevideo.mp4 \
  --modality scene \
  --frame-stride 5
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

The separate API+MCP/Coolify composition belongs to the later server phases and
is not claimed by this image yet.

## Common problems

### FFmpeg is not found

Run `ffmpeg -version` and `ffprobe -version` in the same terminal. Install
FFmpeg or add its executable directory to `PATH`, then rerun `vidxp doctor`.

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
