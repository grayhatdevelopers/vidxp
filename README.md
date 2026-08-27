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
  <strong>Dialogue search · Sound search · Scene search · Action search · Actor grouping</strong>
</p>

<p align="center">
  <a href="https://github.com/grayhatdevelopers/vidxp/releases/latest">
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
- **Action search:** describe something that happens over several seconds.
- **Actor matching:** find recurring faces within a video and export a
  highlighted video for a selected group.

Use it to search years of family videos, add video search to an editing
workflow, or let an AI agent answer questions using evidence from your own
video library. Your videos can stay on your machine.

[![VidXP browser interface](./docs/images/video-screenshot.jpeg)](https://www.linkedin.com/feed/update/urn:li:activity:7343569473720725505/)

## Start here

Choose the setup that fits how you want to use VidXP.

### 1. Desktop app

Download the installer for Windows, Apple Silicon macOS, or Linux from
[GitHub Releases](https://github.com/grayhatdevelopers/vidxp/releases).

Connect an existing VidXP installation or let the desktop app manage an isolated
runtime for you. See the
[Desktop installation instructions](INSTALLATION_GUIDE.md#desktop-app)
for supported setup options.

### 2. CLI and local AI assistants

For commands, scripts, and local AI assistants, install
[uv](https://docs.astral.sh/uv/getting-started/installation/), then run:

```bash
# Install the CPU edition
uv tool install --python 3.14 --torch-backend cpu "vidxp[local-worker,mcp]"

# Check FFmpeg, download models, and verify the installation
vidxp init
vidxp prepare
vidxp doctor

# Print the settings for a local MCP client
vidxp mcp-config
```

Add the browser interface with:

```bash
uv tool install --python 3.14 --torch-backend cpu \
  "vidxp[local-worker,mcp,frontend]"
vidxp ui
```

See the [installation guide](INSTALLATION_GUIDE.md) for client-specific MCP
configuration, the HTTP API, and remote server setup.

### 3. Docker for a server

Run the published all-in-one image on a home server or another single machine:

```bash
# Download the search models into the persistent volume
docker run --rm -it \
  -v vidxp-data:/var/lib/vidxp \
  ghcr.io/grayhatdevelopers/vidxp:latest \
  vidxp prepare

# Start the browser interface
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

- Build searchable libraries from individual videos or whole collections.
- Find dialogue, sound events, visual scenes, and multi-frame actions by description.
- Ask grounded questions and inspect the supporting boards, frames, or clips.
- Group recurring faces and render highlighted actor overlays.
- Keep personal, client, or project libraries separate.
- Use VidXP through the desktop app, browser, CLI, MCP, or HTTP API.

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

# Find an action or event
vidxp search videoprism "a person opens a door and walks outside"

# Find a sound event
vidxp search sound "a dog barking over traffic noise"

# Find something that was said
vidxp search dialogue "the bread just came out of the oven"
```

Results include the source video, timestamps, match score, and the evidence
used to find the moment. Add `--media-id <media-id>` to search only one video.

Run `vidxp --help` or `vidxp <command> --help` for the full command reference.

## For applications and AI agents

Use the Python package to add selected VidXP capabilities directly to an
application, or use the HTTP API when VidXP runs as a service.

[![VidXP being used with ChatGPT Desktop AI](./docs/images/claude-with-vidxp.jpg)](https://youtu.be/fa4Zx-bSOh4)

MCP lets AI clients add and index videos, search a library, ask grounded
questions, and return inspectable evidence such as boards, frames, and clips.
A local client can start VidXP as a program on the same computer. A hosted
client connects to a deployed VidXP server.

### Codex plugin and skills

VidXP is distributed as a Codex plugin through a Git marketplace hosted in
this GitHub repository. It includes three reusable workflows:

- install Desktop or the CLI and connect Codex;
- ingest and index videos; and
- find moments and return inspectable evidence.

Paste this into Codex:

```text
Add https://github.com/grayhatdevelopers/vidxp as a Git plugin marketplace, install the VidXP plugin, then use its $vidxp-install skill to set up VidXP on this computer.
```

VidXP Desktop can perform the same setup from its **Set up in Codex** button.

Compatible AI clients can show an interactive upload and evidence-review view.
Clients without that interface still receive the same workflow results through
ordinary MCP tools.

- [Python, HTTP, and MCP installation](INSTALLATION_GUIDE.md)
- [Local HTTP API and MCP server](docs/local-api.md)
- [ChatGPT and Codex plugin integration](docs/integrations/openai-plugin.md)
- [Optional capability packages](INSTALLATION_GUIDE.md#optional-dependency-extras)
- [Coolify server setup](docs/deployment/coolify.md)

## Downloads and storage

First setup downloads only the models needed for the capabilities you select.
VidXP shows the download size and destination before it starts.

The Desktop-managed Python runtime and its selected dependencies can use
approximately 3 GiB.

| Capability | Approximate model download |
|---|---:|
| Dialogue search | 2.64 GiB |
| Sound event search | 0.94 GiB |
| Scene search | 1.43 GiB |
| Action search | 0.93 GiB |
| Actor matching | 37 MiB |

A full local Desktop setup with every search capability uses approximately
9.0 GiB. Leave additional temporary space during installation and for indexes,
source videos, and exported results.

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
- [Desktop development](docs/desktop.md)
- [Coolify deployment](docs/deployment/coolify.md)
- [Changelog](CHANGELOG.md)
- [Issue tracker](https://github.com/grayhatdevelopers/vidxp/issues)
- [MIT license](LICENSE)

## Contributing

Contributions are welcome. Read the
[contribution guide](docs/CONTRIBUTING.md) before opening a pull request.

## Credits

VidXP began as a student research project by:

- [Abdullah Mansoor](https://github.com/abdullahmansoor321)
- [Muhammad Haroon](https://github.com/haroon10725)
- [Sarah Jawaid](https://github.com/sarr266)
- [Talha Ahmed](https://github.com/talhaahmed1234)

The research was conducted with
[Dr Shahab Tahzeeb](https://scholar.google.com/citations?user=cryeRB0AAAAJ&hl=en)
at [NED University of Engineering and Technology](https://www.neduet.edu.pk/)
and [Saad Bazaz](https://scholar.google.com/citations?user=mrJo09oAAAAJ&hl=en)
at [Grayhat](https://grayhat.studio/).

VidXP is now built by [Grayhat](https://grayhat.studio/) and maintained by
community contributors.

Email: <info@grayhat.studio>

<a href="https://github.com/grayhatdevelopers/vidxp/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=grayhatdevelopers/vidxp" alt="VidXP contributors">
</a>
