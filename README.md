<p align="center">
  <a href="https://github.com/grayhatdevelopers/vidxp">
    <img src="./docs/images/logo.png" alt="VidXP logo" width="180">
  </a>
</p>

<h1 align="center">VidXP</h1>

<p align="center">
  <em>Search video by what was said, what appeared on screen, and recurring faces.</em>
</p>

<p align="center">
  A local-first video search engine for people, applications, and AI agents.
</p>

<p align="center">
  <strong>Dialogue search · Scene search · Actor grouping</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/vidxp/"><img src="https://img.shields.io/pypi/v/vidxp" alt="PyPI version"></a>
  <a href="https://github.com/grayhatdevelopers/vidxp/pkgs/container/vidxp"><img src="https://img.shields.io/badge/container-GHCR-blue" alt="GHCR container"></a>
  <a href="https://github.com/grayhatdevelopers/vidxp/actions/workflows/ci.yml?query=branch%3Amain"><img src="https://github.com/grayhatdevelopers/vidxp/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI status"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/grayhatdevelopers/vidxp" alt="MIT license"></a>
  <a href="https://grayhat.studio/discord"><img src="https://img.shields.io/discord/867124708473700363?logo=discord&logoColor=white" alt="Discord"></a>
</p>

## Find the moment, not the timestamp

VidXP turns one video or a whole collection into a persistent, searchable
library. Search by meaning instead of filenames, tags, or exact words:

- **Dialogue:** find what was said from timestamped transcripts.
- **Scenes:** describe what appeared on screen and retrieve matching moments.
- **Actors:** group recurring faces within a video and export a highlighted
  overlay for a selected group.

Use it to find relatives across years of wedding videos, add semantic video
search to an editing application, or give an AI agent a grounded way to
understand a video library.

Processing and search run locally after the selected models have been
downloaded.

[![VidXP browser interface](./docs/images/video-screenshot.jpeg)](https://www.linkedin.com/feed/update/urn:li:activity:7343569473720725505/)

## Install and run VidXP

### 1. Native CLI and MCP

For scripts, local agent tools, and direct control over indexing and search,
install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```bash
# Install isolated CPU runtime
uv tool install --python 3.14 --torch-backend cpu "vidxp[local-worker,mcp]"

# Check required system tools
vidxp init

# Review and download models
vidxp prepare

# Verify the installation
vidxp doctor

# Print MCP client config
vidxp mcp-config
```

The CLI works without MCP. To include the local browser app as well, install
`vidxp[local-worker,mcp,frontend]` and run `vidxp ui`.

If `vidxp` is not immediately available after installation, run
`uv tool update-shell` once and reopen the terminal.

### 2. Desktop app

For a managed local installation, download VidXP from
[GitHub Releases](https://github.com/grayhatdevelopers/vidxp/releases). The
desktop app owns its Python environment, CPU dependencies, model preparation,
and local worker; the user does not need to install Python or uv.

Installers for Windows x86-64, Apple Silicon macOS, and Linux x86-64 are
published with each release. On first launch, VidXP checks the machine and
configures only the capabilities and interfaces selected by the user. The
browser interface and its Python dependencies can be omitted for a
processing-only runtime.

After configuration, the desktop supervisor runs in the system tray.
**Open VidXP** opens or reuses the local browser interface, and **Quit VidXP**
stops its owned processes. See [Desktop application](docs/desktop.md) for
behavior and source builds.

### 3. Docker for servers

Stable releases publish three Linux/amd64 images to the
[VidXP GitHub Container Registry](https://github.com/grayhatdevelopers/vidxp/pkgs/container/vidxp):

| Image tag | Purpose |
|---|---|
| `ghcr.io/grayhatdevelopers/vidxp:<release>` | All-in-one CPU worker and browser app |
| `ghcr.io/grayhatdevelopers/vidxp:<release>-control` | HTTP API, remote MCP, migrations, and upload hooks |
| `ghcr.io/grayhatdevelopers/vidxp:<release>-worker` | CPU model worker for the server deployment |

The supported [Coolify deployment](docs/deployment/coolify.md) uses the
published `control` and `worker` images with `compose.coolify.yaml`; it does not
build VidXP from a repository checkout. The deployment includes PostgreSQL,
Chroma, resumable uploads, persistent media and model volumes, and static
bearer or OIDC authentication.

Use immutable release tags or image digests for a server. The all-in-one image
is available when one machine only needs the local browser product:

```bash
docker run --rm --init \
  -p 8501:8501 \
  -v vidxp-data:/var/lib/vidxp \
  ghcr.io/grayhatdevelopers/vidxp:<release>
```

## Use VidXP your way

| Surface | What it is for |
|---|---|
| Browser app | Import videos, prepare models, index, search, inspect progress, and download results |
| Desktop app | A managed local runtime, optional browser interface, and worker |
| CLI | Scriptable media, indexing, search, job, repository, and artifact workflows |
| Python API | Embed selected indexing and retrieval capabilities in another application |
| HTTP API | Build applications on a versioned service contract |
| MCP | Let local or remote agents discover media, index it, search it, and create clips |
| Containers | Run the local browser product or a separated API/worker deployment |

All of these surfaces use the same application layer and repository contracts;
they are not separate implementations.

## What is available now

- Persistent libraries containing one video or many.
- Semantic dialogue search with timestamped evidence.
- Text-to-scene retrieval across indexed videos.
- Within-video face grouping and highlighted actor overlays.
- Cross-video top-k search with optional single-video filtering.
- Durable jobs with progress, cancellation, recovery, and retained results.
- Atomic index snapshots, so a failed rebuild does not replace a working one.
- Managed media, downloadable clips, overlays, and artifact metadata.
- Named repositories for keeping collections separate.
- Browser, CLI, Python, HTTP, MCP, desktop, and container interfaces.
- Local CPU execution and a separated self-hosted server topology.

## A first search

The browser app provides the guided workflow. The equivalent CLI flow is:

```bash
vidxp media import samplevideo.mp4 --json
vidxp index create <media-id>
vidxp search scene "a yellow taxi on a city street"
vidxp search dialogue "the bread just came out of the oven"
```

Searches return ranked moments with video identity, timestamps, scores, and
capability-specific evidence. Use `--media-id` to stay within one video or omit
it to search the active collection.

Run `vidxp --help` or `vidxp <command> --help` for the complete CLI reference.

## For applications and agents

Applications can embed individual capabilities through the Python package or
run VidXP as a local or authenticated remote service. Agents can connect over
stdio MCP on the same machine or Streamable HTTP MCP on a server.

The MCP surface can register or discover media, create indexes, search dialogue,
scenes, and actors, ask grounded questions, create clips and overlays, poll
durable jobs, and retrieve artifact download links.

- [Python installation and capability extras](INSTALLATION_GUIDE.md#optional-dependency-extras)
- [HTTP and MCP installation profiles](INSTALLATION_GUIDE.md)
- [OpenAPI and MCP transport behavior](docs/architecture/platform.md)
- [Coolify and server deployment](docs/deployment/coolify.md)

## Models and local data

Model weights are not hidden inside the Python package or container image. The
browser app and `vidxp prepare` disclose what is missing, the download size,
the cache location, and available disk space before downloading.

| Capability | Models | Approximate download |
|---|---|---:|
| Dialogue | Qwen3 Embedding 0.6B + faster-whisper large-v3-turbo | 2.64 GiB |
| Scene | SigLIP2 base patch16-224 | 1.43 GiB |
| Actor | OpenCV Zoo YuNet + SFace | 37 MiB |

Actual installed and cached disk use is higher because the Python runtime,
PyTorch, provider packages, indexes, source media, and generated artifacts are
separate from model weights.

Local data lives in the operating system's per-user VidXP directory, not in
the shell's current directory:

| Platform | Default data root |
|---|---|
| Windows | `%LOCALAPPDATA%\VidXP` |
| macOS | `~/Library/Application Support/VidXP` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/VidXP` |

CLI and desktop installations share this data root by default. The desktop
keeps its managed Python environment and launcher state in a separate private
application-data directory.

Docker stores the same product data in the declared `vidxp-data` volume.

## What is next

The current product work is focused on:

- signing and notarizing downloadable desktop packages;
- improving desktop runtime repair and configuration controls;
- evaluating supported GPU worker and desktop profiles;
- richer result playback, previews, timelines, and actor-labeling workflows;
- stronger collection organization and search filtering;
- benchmark-backed retrieval and ranking improvements; and
- scaling the server topology beyond its current single-node deployment.

VidXP is beta software. Current boundaries are documented rather than hidden:
CPU is the supported runtime today, actor matching groups appearances within a
video rather than identifying real people, and the published server topology
does not yet claim multi-replica failover.

## Documentation

| Use or deploy VidXP | Build or evaluate VidXP |
|---|---|
| [Installation guide](INSTALLATION_GUIDE.md) | [Contribution guide](docs/CONTRIBUTING.md) |
| [Desktop application](docs/desktop.md) | [Adding a capability](docs/adding-a-capability.md) |
| [Coolify deployment](docs/deployment/coolify.md) | [Architecture](docs/architecture/platform.md) |
| [Changelog](CHANGELOG.md) | [Benchmarking](docs/benchmarking/README.md) |

## Contributing

Contributions are welcome. Read the
[contribution guide](docs/CONTRIBUTING.md) before opening a pull request.

## Credits

Built by Grayhat Developers PVT Ltd. and maintained by the community.

Email: info@grayhat.studio

<a href="https://github.com/grayhatdevelopers/vidxp/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=grayhatdevelopers/vidxp" alt="VidXP contributors">
</a>
