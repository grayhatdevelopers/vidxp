<p align="center">
  <a href="https://github.com/grayhatdevelopers/vidxp">
    <img src="./docs/images/logo.png" alt="VidXP logo" width="180">
  </a>
</p>

<h1 align="center">VidXP</h1>

<p align="center">
  <strong>Search video by what was said, what appeared, and who was on screen.</strong>
</p>

<p align="center">
  Local-first video indexing for people, applications, and AI agents.
</p>

<p align="center">
  <a href="https://pypi.org/project/vidxp/"><img src="https://img.shields.io/pypi/v/vidxp" alt="PyPI version"></a>
  <a href="https://pypi.org/project/vidxp/"><img src="https://img.shields.io/pypi/pyversions/vidxp" alt="Supported Python versions"></a>
  <a href="https://github.com/grayhatdevelopers/vidxp/actions/workflows/ci.yml?query=branch%3Amain"><img src="https://github.com/grayhatdevelopers/vidxp/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI status"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/grayhatdevelopers/vidxp" alt="MIT license"></a>
  <a href="https://grayhat.studio/discord"><img src="https://img.shields.io/discord/867124708473700363?logo=discord&logoColor=white" alt="Discord"></a>
</p>

## What VidXP does

VidXP turns one video or a whole local collection into a persistent,
searchable index. Models run on the worker rather than in the browser, API, or
MCP process, and model downloads happen only after explicit confirmation.

| Dialogue | Scenes | Actors |
|---|---|---|
| Transcribes speech and indexes timestamped phrases | Samples video at a source-aware cadence and embeds frames | Detects faces and groups similar appearances within a video |
| Search by meaning, not only exact words | Search with descriptions such as “a red car at night” | Inspect clusters and render highlighted actor-overlay videos |

The same repository is available through every supported surface:

| CLI | Browser and desktop | HTTP API and MCP | Docker |
|---|---|---|---|
| Import, index, search, query, jobs, and clips | Guided local workflow with visible preparation and job progress | Typed control plane for applications and agents | Local all-in-one image or separated server control/worker images |

Other product-level behavior includes:

- multi-video snapshots with cross-video top-k search;
- optional `media_id` filtering for one-video search;
- durable indexing, search, model-preparation, and artifact jobs;
- managed media, actor overlays, and downloadable video clips;
- grounded natural-language answers that retain timestamped evidence; and
- named repositories for separate searchable collections.

[![VidXP browser interface](./docs/images/video-screenshot.jpeg)](https://www.linkedin.com/feed/update/urn:li:activity:7343569473720725505/)

## Start here: local browser app

This is the native end-user sequence. It installs the CPU worker and browser
interface from PyPI into an isolated virtual environment. Install
[uv](https://docs.astral.sh/uv/getting-started/installation/) first, then verify
it is available:

```bash
uv --version
```

### 1. Create and activate the environment

```bash
uv venv --python 3.14
```

| Windows PowerShell | macOS or Linux |
|---|---|
| `.venv\Scripts\Activate.ps1` | `source .venv/bin/activate` |

### 2. Install VidXP

```bash
uv pip install --torch-backend cpu "vidxp[local-worker,frontend]"
```

### 3. Initialize and prepare

```bash
vidxp init
vidxp prepare
vidxp doctor
```

- `init` verifies FFmpeg, ffprobe, `libx264`, and `aac`. If FFmpeg is missing,
  it shows the exact supported package-manager command and asks before running
  it.
- `prepare` lists each missing model, download size, cache location, and total
  additional space before asking for confirmation.
- `doctor` is read-only. It installs and downloads nothing.

### 4. Launch

```bash
vidxp ui
```

Open `http://localhost:8501` if it does not open automatically. VidXP stores
its repository and model cache in the operating system’s per-user application
data directory, not in the directory where the command was run.

See the [installation guide](INSTALLATION_GUIDE.md) for a CLI-only worker,
MCP, a local API, Docker, macOS-specific installation, and troubleshooting.

## Start here: Docker

The local image contains the CPU worker, browser UI, and FFmpeg, but no model
weights.

```bash
git clone https://github.com/grayhatdevelopers/vidxp.git
cd vidxp
docker compose pull
docker compose run --rm vidxp vidxp prepare
docker compose up
```

The UI is available at `http://localhost:8501`. The `vidxp-data` volume keeps
media, indexes, jobs, and models across container replacement.

## Installation profiles

Install only the surfaces that belong in the same process environment.

| Profile or extra | Adds | Use it for |
|---|---|---|
| Base `vidxp` | Lightweight CLI, typed contracts, repository configuration, `init` | Version/configuration commands or building a custom client; not local indexing |
| `storage` | Embedded Chroma and host monitoring | Custom local storage integrations |
| `dialogue` | Storage, faster-whisper, sentence-transformers | Dialogue-only local indexing |
| `scene` | Storage, PyTorch, Transformers, OpenCV, Pillow | Scene-only local indexing |
| `actor` | Storage, OpenCV, YuNet/SFace support | Actor-only local indexing |
| `all` | Dialogue, scene, and actor providers | A custom full worker without the query-model client |
| `local-worker` | All providers plus the optional grounded-query client | Recommended complete local worker |
| `frontend` | Streamlit | Browser UI; combine with a local worker |
| `mcp` | MCP SDK | Local stdio MCP; combine with a local worker |
| `server` | FastAPI, remote MCP, PostgreSQL control-plane dependencies | API/MCP control image or a local API when combined with `local-worker` |
| `server-worker` | Server storage client plus every CPU provider | Separated server worker image |
| `benchmarks` | DiDeMo and HiREST adapter dependencies | Evaluation and benchmark development |
| `test` | Pytest and HTTP test client | Contributor validation only |

Common combinations:

| Goal | Package selection | Start command |
|---|---|---|
| Local CLI worker | `vidxp[local-worker]` | `vidxp --help` |
| Local browser app | `vidxp[local-worker,frontend]` | `vidxp ui` |
| Local stdio MCP | `vidxp[local-worker,mcp]` | `vidxp-mcp` |
| Local API + remote MCP | `vidxp[local-worker,server]` | `vidxp-api` |
| Everything local | `vidxp[local-worker,frontend,server]` | `vidxp ui` or `vidxp-api` |

Python package installation never changes operating-system packages. Run
`vidxp init` explicitly for guided FFmpeg setup.

## Index and search from the CLI

After initialization and model preparation:

```bash
vidxp media import samplevideo.mp4 --json
vidxp index create <media-id>
vidxp search dialogue "the bread just came out of the oven"
vidxp search scene "a yellow taxi on a city street" --top-k 5
vidxp query "What happens after the taxi arrives?"
```

The import command copies and validates the video in managed storage and
returns a stable `media_id`.

- Omit `--media-id` to rank evidence across every video in the active snapshot.
- Add `--media-id <media-id>` to restrict `search` or `query` to one video.
- Run `vidxp media list` to rediscover filenames and IDs.
- Run `vidxp index list` to see which registered videos are searchable.
- Re-indexing one video replaces only that video’s generation.
- Use separate named repositories when collections must not be searched
  together.

Index selected capabilities or change scene detail:

```bash
vidxp index create <media-id> \
  --modality scene \
  --scene-sample-fps 1
```

Scene sampling is time-based and normalized to the source frame rate.
Lower-frame-rate videos use every available frame rather than duplicating
frames.

### Jobs and downloadable clips

Long-running commands wait by default. Add `--detach`, then inspect the durable
job:

```bash
vidxp jobs list
vidxp jobs show <job-id>
vidxp jobs cancel <job-id>
vidxp jobs retry <job-id>
```

Create and export a result clip:

```bash
vidxp artifacts snippet <media-id> 30 45 --json
vidxp artifacts download <artifact-id> ./clip.mp4
```

Run `vidxp --help` or `vidxp <command> --help` for the complete command
reference.

## MCP for local agents

Install the local worker and MCP extra in the same environment, prepare the
models, and print client-ready configuration:

```bash
uv pip install --torch-backend cpu "vidxp[local-worker,mcp]"
vidxp init
vidxp prepare
vidxp mcp-config
```

`vidxp mcp-config` prints a complete `mcpServers` JSON object containing the
absolute `vidxp-mcp` executable path. Copy the object into any stdio MCP client.

Verify the handshake, tool discovery, repository paths, and a read-only status
call:

```bash
vidxp-mcp --check --repository default
```

Agents can discover media, index videos, search across the active snapshot,
ask grounded questions, create clips, poll jobs, and retrieve artifact
download links. MCP does not carry video bytes or expose server filesystem
paths.

## HTTP API

For a local API backed by the same local worker:

```bash
uv pip install --torch-backend cpu "vidxp[local-worker,server]"
vidxp init
vidxp prepare
vidxp-api
```

The default server binds to `127.0.0.1:8000`.

| Reference | URL |
|---|---|
| Interactive OpenAPI | `http://127.0.0.1:8000/docs` |
| OpenAPI document | `http://127.0.0.1:8000/openapi.json` |
| Health | `http://127.0.0.1:8000/health` |
| Readiness | `http://127.0.0.1:8000/ready` |
| Streamable HTTP MCP | `http://127.0.0.1:8000/mcp` |

The API exposes media ingestion and delivery, capability discovery, durable
jobs, index state, search/query jobs, and artifacts under `/api/v1`. Remote
uploads larger than the multipart limit use the server deployment’s resumable
tus endpoint.

Non-loopback deployments must use static bearer or OIDC authentication. Use
the supported [Coolify/Compose topology](docs/deployment/coolify.md) instead of
turning a local process into an ad-hoc public server.

## Containers and server deployment

Stable releases publish three Linux/amd64 images from the same release commit:

| Image | Tag | Contains | Intended use |
|---|---|---|---|
| Local | `ghcr.io/grayhatdevelopers/vidxp:<release>` and `latest` | CPU local worker, Streamlit, FFmpeg | One-machine browser app with `compose.yaml` |
| Control | `ghcr.io/grayhatdevelopers/vidxp:<release>-control` | FastAPI, remote MCP, migrations, hooks | Public API/MCP control plane |
| Worker | `ghcr.io/grayhatdevelopers/vidxp:<release>-worker` | CPU providers and HTTP Chroma client | Model-backed server jobs |

`compose.coolify.yaml` combines the immutable control and worker images with
pinned PostgreSQL, Chroma, tusd, and an optional Ollama service. It is the
supported server topology for Coolify and ordinary Docker Compose:

- one API/MCP service;
- one CPU worker;
- resumable remote uploads;
- persistent database, vector, media, and model volumes; and
- static bearer or OIDC authentication.

Follow the [Coolify deployment guide](docs/deployment/coolify.md) for required
secrets, hostnames, health checks, proxy headers, and backup boundaries.

## Desktop application

The Tauri desktop application provides a guided first-run installer and then
launches the same local browser interface:

- checks FFmpeg before downloading Python or VidXP;
- asks before running a supported system package manager;
- installs an app-owned Python/runtime profile;
- optionally prepares selected models; and
- activates the runtime only after `vidxp doctor` passes.

Desktop packaging is currently a preview built from source; signed installer
publication is still under release validation. See
[Desktop application](docs/desktop.md).

## Python API

VidXP can also be embedded as a Python indexing/retrieval layer. Install only
the capability extras your application uses:

```bash
uv pip install --torch-backend cpu "vidxp[scene]"
```

```python
from vidxp.core import IndexConfig, VideoSource
from vidxp.core.runner import run_index
from vidxp.capabilities.scene.operations import search_scene

config = IndexConfig(
    dataset="my-library",
    split="local",
    run_id="demo",
    enabled_modalities=("scene",),
    capability_options={"scene": {"sample_fps": 1.0}},
)

run_index(
    [VideoSource(video_id="video-1", path="videos/first.mp4")],
    config,
)

for hit in search_scene("a person enters a taxi", config=config, top_k=5).hits:
    print(hit.video_id, hit.start, hit.end, hit.score)
```

See the [Python indexing contract](docs/benchmarking/core_contract.md) for
configuration, checkpoints, result schemas, and run layout.

## Models, downloads, and local data

Pinned model downloads are disclosed before preparation:

| Capability | Models | Approximate download |
|---|---|---:|
| Dialogue | Qwen3 Embedding 0.6B + faster-whisper large-v3-turbo | 2.64 GiB |
| Scene | SigLIP2 base patch16-224 | 1.43 GiB |
| Actor | OpenCV Zoo YuNet + SFace | 37 MiB |

Actual installed and cached disk use can be higher. `vidxp prepare` calculates
what is missing on the current machine and asks before downloading it.

Local repositories and models are independent of the shell’s current
directory and Python environment:

| Platform | Default data root |
|---|---|
| Windows | `%LOCALAPPDATA%\VidXP` |
| macOS | `~/Library/Application Support/VidXP` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/VidXP` |

The default repository is under `repositories/default/`; models are under
`models/`. Use `--data-dir` for one CLI invocation or `VIDXP_DATA_DIR` for all
local entry points. Docker uses its declared volumes instead.

## Current release boundaries

- CPU is the supported default. GPU execution is explicitly deferred.
- Model preparation is explicit; first indexing/search requests never begin
  hidden downloads.
- The desktop app is a preview until signed installers are published.
- The server Compose topology is a single-node, single-repository deployment;
  multi-replica failover and hosted database substitutions are not claimed.
- Actor identity is within-video face grouping, not real-world identity
  recognition.

## Documentation

| Use VidXP | Build or evaluate VidXP |
|---|---|
| [Installation and troubleshooting](INSTALLATION_GUIDE.md) | [Contribution guide](docs/CONTRIBUTING.md) |
| [Coolify deployment](docs/deployment/coolify.md) | [Adding a capability](docs/adding-a-capability.md) |
| [Desktop preview](docs/desktop.md) | [Benchmarking](docs/benchmarking/README.md) |
| [Changelog](CHANGELOG.md) | [Release process](docs/releasing.md) |

## Contributing

Contributions are welcome. Please read the
[contribution guide](docs/CONTRIBUTING.md) before opening a pull request.

## Credits

Built by Grayhat Developers PVT Ltd. and maintained by the community.

- Email: info@grayhat.studio
- [Issue tracker](https://github.com/grayhatdevelopers/vidxp/issues)
- [Discord](https://grayhat.studio/discord)
- [MIT license](LICENSE)

<a href="https://github.com/grayhatdevelopers/vidxp/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=grayhatdevelopers/vidxp" alt="VidXP contributors">
</a>
