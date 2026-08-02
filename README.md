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
  <a href="https://github.com/grayhatdevelopers/vidxp/releases">
    <img src="https://img.shields.io/badge/Download-Desktop_app-5865F2?style=for-the-badge&logo=github" alt="Download VidXP desktop app">
  </a>
</p>

<p align="center">
  Windows · Apple Silicon macOS · Linux
</p>

<p align="center">
  <a href="https://pypi.org/project/vidxp/"><img src="https://img.shields.io/pypi/v/vidxp" alt="PyPI version"></a>
  <a href="https://github.com/grayhatdevelopers/vidxp/pkgs/container/vidxp"><img src="https://img.shields.io/badge/container-GHCR-blue" alt="GHCR container"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/grayhatdevelopers/vidxp" alt="MIT license"></a>
  <a href="https://grayhat.studio/discord"><img src="https://img.shields.io/discord/867124708473700363?logo=discord&logoColor=white" alt="Discord"></a>
</p>

## Find the moment, not the timestamp

VidXP makes one video—or an entire collection—searchable by meaning:

- **Dialogue search:** type what you remember someone saying and jump to the
  matching moments.
- **Scene search:** describe what appeared on screen and find the closest
  visual matches.
- **Actor matching:** find recurring faces within a video and export a
  highlighted video for a selected group.

Use it to search years of family videos, add video search to an editing
workflow, or let an AI agent answer questions using evidence from your own
video library. Your videos can stay on your machine.

[![VidXP browser interface](./docs/images/video-screenshot.jpeg)](https://www.linkedin.com/feed/update/urn:li:activity:7343569473720725505/)

## Start here

Choose the setup that fits how you want to use VidXP.

### 1. CLI and MCP

For direct use, scripts, and local AI agents, install
[uv](https://docs.astral.sh/uv/getting-started/installation/), then run:

```bash
# Install the CPU edition
uv tool install --python 3.14 --torch-backend cpu "vidxp[local-worker,mcp]"

# Set up FFmpeg
vidxp init

# Download search models
vidxp prepare

# Check everything
vidxp doctor

# Connect an MCP client
vidxp mcp-config
```

`vidxp mcp-config` emits the `mcpServers` JSON used by Claude Desktop and
compatible local stdio clients. Codex has separate configuration: use
`codex mcp add vidxp -- vidxp-mcp --repository default` or configure
`[mcp_servers.vidxp]` in `~/.codex/config.toml`. ChatGPT Desktop and ChatGPT web
use ChatGPT's own connector configuration and do not read Codex's config; hosted
ChatGPT connections use a remote MCP endpoint, with VidXP deployed behind HTTPS
and OIDC.

The CLI works without MCP. Add the browser app with:

```bash
uv tool install --python 3.14 --torch-backend cpu \
  "vidxp[local-worker,mcp,frontend]"
vidxp ui
```

`vidxp ui` binds to loopback by default. Use `vidxp ui --share` only when you
intend to expose the unauthenticated browser interface on the local network.
Streamlit prints its Local and Network URLs when it starts. VidXP disables
Streamlit's first-run email prompt and usage-statistics collection.

`vidxp-api --share` exposes a bearer-protected HTTP API/MCP endpoint on a
trusted LAN. Browser upload tools are omitted unless an explicit HTTPS upload
handoff origin is configured, because a LAN listener alone cannot advertise a
safe browser capability page.

If the `vidxp` command is not found, run `uv tool update-shell` once and reopen
the terminal.

### 2. Desktop app

Download the installer for Windows, Apple Silicon macOS, or Linux from
[GitHub Releases](https://github.com/grayhatdevelopers/vidxp/releases).

On first launch, choose whether to adopt an existing compatible VidXP
installation without downloading another runtime, or create a private runtime
managed by VidXP Desktop. For a managed runtime, choose the search capabilities,
model location, and optional browser interface; Python and uv do not need to be
installed separately.

Desktop opens its control panel when started. Browser launch is a separate,
explicit **Open VidXP** action. After configuration, closing the control panel
keeps VidXP available through **Manage VidXP**, **Open VidXP**, and **Quit
VidXP** in the system tray.

### 3. Docker for a server

Run the published all-in-one image on a home server or another single machine:

```bash
docker run --rm --init \
  -p 8501:8501 \
  -v vidxp-data:/var/lib/vidxp \
  ghcr.io/grayhatdevelopers/vidxp:latest
```

For a long-lived server, pin a published version instead of `latest`. For a
Coolify deployment, use the published `-control` and `-worker` images with
[`compose.coolify.yaml`](compose.coolify.yaml)—no repository build is required.
See the [Coolify guide](docs/deployment/coolify.md) for the complete setup.

## What you can do today

- Build a reusable search library from one video or a whole collection.
- Search dialogue by meaning, even when you do not remember the exact words.
- Find visual moments by describing the scene you are looking for.
- Group recurring faces in a video and render a highlighted actor overlay.
- Search one selected video or every video in the active library.
- Open matching timestamps and export downloadable clips and overlays.
- Retrieve completed clips through native MCP resources, local stdio paths, or
  short-lived resumable HTTPS downloads without embedding video bytes in tool JSON.
- Keep personal, client, or project libraries separate.
- Follow long indexing jobs, cancel them, and keep the last working index if a
  later run fails.
- Use the browser app, automate the CLI, connect an MCP agent, or integrate
  VidXP into another application.

## A first search

The browser app guides you through importing and indexing. The same flow from
the command line is:

```bash
# Add a video
vidxp media import samplevideo.mp4 --json

# Index the returned media ID
vidxp index create <media-id>

# Find a visual moment
vidxp search scene "a yellow taxi on a city street"

# Find something that was said
vidxp search dialogue "the bread just came out of the oven"
```

Results include the source video, timestamps, match score, and the evidence
used to find the moment. Add `--media-id <media-id>` to search only one video.

Run `vidxp --help` or `vidxp <command> --help` for the full command reference.

## For applications and AI agents

Use the Python package to add selected VidXP capabilities directly to an
application, or use the HTTP API when VidXP runs as a service.

MCP clients can add and discover videos, start indexing, search dialogue and
scenes, ask questions about a library, and create clips or actor overlays.
Local agents can connect over stdio; remote agents can connect to a
self-hosted VidXP server.

Search and question results can include directly inspectable frames and clips, so
agents can show the evidence behind an answer without making users translate raw
timestamps. Evidence rendering is best-effort: a result can still be useful when
an individual frame or clip cannot be produced.

Remote agents can hand users a short-lived page for selecting and uploading
multiple videos. Local agents can ingest approved filesystem paths without moving
video bytes through MCP. VidXP normally indexes successful imports automatically;
registration-only ingestion stops at `registered`, and indexing failures can be
retried without uploading the video again.

Agents can call `get_workspace` before acting to inspect registered media,
active-index coverage, model readiness, and the searchable, queryable,
inspectable, or renderable roles available for each video. Invalid capability
or media selections are rejected before a durable job is queued and include an
actionable next step.

### ChatGPT and Codex skills

VidXP includes reusable skill source folders for the two common agent workflows:

- [Ingest and index videos](skills/vidxp-ingest-video/SKILL.md)
- [Find moments and return inspectable evidence](skills/vidxp-find-video-evidence/SKILL.md)

Download a skill folder and add it through a supported ChatGPT desktop or Codex
Skills surface. The skills require a connected VidXP MCP server; installable
plugin packaging for additional ChatGPT surfaces will follow separately.

- [Python, HTTP, and MCP installation](INSTALLATION_GUIDE.md)
- [Optional capability packages](INSTALLATION_GUIDE.md#optional-dependency-extras)
- [Coolify server setup](docs/deployment/coolify.md)

## Downloads and storage

First setup downloads only the models needed for the capabilities you select.
VidXP shows the download size and destination before it starts.

| Capability | Approximate model download |
|---|---:|
| Dialogue search | 2.64 GiB |
| Scene search | 1.43 GiB |
| Actor matching | 37 MiB |

Leave additional space for the VidXP runtime, indexes, source videos, and
exported results.

By default, the CLI and desktop app share the same VidXP data directory:

| Platform | Default location |
|---|---|
| Windows | `%LOCALAPPDATA%\VidXP` |
| macOS | `~/Library/Application Support/VidXP` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/VidXP` |

Docker keeps the same data in the `vidxp-data` volume shown above.

## Product roadmap

The next product improvements are focused on:

- labeling actor groups and matching the same person across different videos;
- more reliable face tracking across angle, lighting, motion, and occlusion;
- connecting visible people with the dialogue they are speaking;
- better search ranking, time ranges, and natural-language questions across a
  whole library;
- richer previews, timelines, filters, saved searches, and result playback;
- easier organization for large personal and project video collections;
- faster indexing and supported GPU acceleration; and
- smoother desktop updates, repair, and model management.

VidXP is in beta. Feedback about search quality, actor workflows, and real
video-library use cases is especially useful.

## Help and project links

- [Installation and troubleshooting](INSTALLATION_GUIDE.md)
- [Desktop application](docs/desktop.md)
- [Coolify deployment](docs/deployment/coolify.md)
- [Changelog](CHANGELOG.md)
- [Issue tracker](https://github.com/grayhatdevelopers/vidxp/issues)
- [MIT license](LICENSE)

## Contributing

Contributions are welcome. Read the
[contribution guide](docs/CONTRIBUTING.md) before opening a pull request.

## Credits

Built by Grayhat Developers PVT Ltd. and maintained by the community.
Originally researched by students:
- [Abdullah Mansoor](https://github.com/abdullahmansoor321)
- [Muhammad Haroon](https://github.com/haroon10725)
- [Sarah Jawaid](https://github.com/sarr266)
- [Talha Ahmed](https://github.com/talhaahmed1234)

Working with [Dr Shahab Tahzeeb](https://scholar.google.com/citations?user=cryeRB0AAAAJ&hl=en) ([NED University of Engineering and Technology](https://www.neduet.edu.pk/)) and [Saad Bazaz](https://scholar.google.com/citations?user=mrJo09oAAAAJ&hl=en) ([Grayhat](https://grayhat.studio).

Email: info@grayhat.studio

<a href="https://github.com/grayhatdevelopers/vidxp/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=grayhatdevelopers/vidxp" alt="VidXP contributors">
</a>
