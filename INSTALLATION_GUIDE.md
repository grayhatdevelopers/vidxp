# Install VidXP

For the simplest setup, install the Desktop app. Use the command line when you
want scripting, local MCP, or direct control over which interfaces are
installed.

## Choose a setup

| What you want | Recommended setup |
|---|---|
| A normal desktop application | [Desktop app](#desktop-app) |
| Commands, scripts, or a local AI assistant | [Command line](#command-line) |
| A local browser app without installing Python | [Docker](#docker) |
| A server reachable over HTTPS | [Self-hosted server](#self-hosted-server) |

VidXP currently supports Windows x86-64, Linux x86-64, and Apple Silicon macOS
14 or newer. CPU processing is supported; GPU setup is not yet supported.

## Before you install

Desktop and command-line setups need:

- internet access during first setup;
- FFmpeg and ffprobe with the `libx264` and `aac` codecs; and
- free space for the application, selected models, indexes, and videos.

VidXP checks FFmpeg during setup and shows an installation command when it is
missing. Windows and macOS can run the suggested WinGet or Homebrew command
only after you approve it. Linux shows a command for you to run. Docker already
includes FFmpeg.

Approximate model downloads are:

| Feature | Download |
|---|---:|
| Dialogue search | 2.64 GiB |
| Sound event search | 0.94 GiB |
| Scene search | 1.43 GiB |
| Action search | 0.93 GiB |
| Actor matching | 37 MiB |

The Desktop-managed runtime can use about 3 GiB in addition to selected models.
A complete local setup uses about 8.1 GiB before adding videos and indexes.

## Desktop app

### 1. Download and open VidXP

Download the package for your operating system from
[GitHub Releases](https://github.com/grayhatdevelopers/vidxp/releases):

- Windows: NSIS installer
- Apple Silicon macOS: DMG
- Linux x86-64: AppImage

Beta and stable macOS releases are signed with a Developer ID certificate,
notarized by Apple, and stapled. The Windows installer is not yet signed, so
Windows SmartScreen may ask you to confirm that you want to open it. Confirm
only when you downloaded the package from the official VidXP release page.

### 2. Choose how Desktop should run VidXP

On first launch, choose one option:

- **Set up VidXP for me** — recommended for most people. Desktop installs and
  manages its own private Python and VidXP environment.
- **Use an existing installation** — use a compatible `vidxp` command already
  installed on your computer.

Desktop shows what it will install before making changes. A failed or cancelled
setup leaves the previous working setup available.

### 3. Choose features

- **Local video processing** imports, indexes, searches, and creates video
  results on this computer.
- **Browser interface** adds the full VidXP browser app.
- **AI assistant integration** lets a local MCP-compatible assistant use VidXP.
- **App integration service** lets other local applications use the HTTP API or
  Streamable HTTP MCP.

Choose where models should be stored, then decide whether to download them
during setup. VidXP displays the required downloads before starting them.

### 4. Open VidXP

After setup finishes:

- select **Open VidXP** to start the browser app;
- select **Check downloaded models** to verify or download selected models;
- use **Setup options** to add or remove features; or
- use **Quit VidXP** from the tray to stop Desktop-managed services.

Browser and API sharing are off by default. If you enable sharing, use it only
on a trusted local network and follow the warning shown by Desktop.

## Command line

Command-line installation uses
[uv](https://docs.astral.sh/uv/getting-started/installation/) to install an
isolated Python tool. You do not need to create or activate a virtual
environment.

### 1. Install VidXP

Install the CLI, local processing, and local MCP support:

```bash
uv tool install --python 3.14 --torch-backend cpu \
  "vidxp[local-worker,mcp]"
```

The name inside brackets selects the features installed with VidXP. Choose the
complete set you need:

| What you need | Package profile |
|---|---|
| CLI and local processing | `vidxp[local-worker]` |
| Browser app | `vidxp[local-worker,frontend]` |
| Local MCP | `vidxp[local-worker,mcp]` |
| Local HTTP API and remote MCP | `vidxp[local-worker,server]` |
| Browser app, API, and MCP | `vidxp[local-worker,frontend,mcp,server]` |

To add or remove features later, reinstall the tool with the complete set you
want to keep. For example:

```bash
uv tool install --force --python 3.14 --torch-backend cpu \
  "vidxp[local-worker,frontend,mcp]"
```

If the `vidxp` command is not found, run `uv tool update-shell`, close the
terminal, and open it again.

### 2. Check FFmpeg

```bash
vidxp init
```

VidXP checks FFmpeg, ffprobe, `libx264`, and `aac`. Review any suggested system
installation command before approving it.

If FFmpeg is already installed somewhere unusual, provide its paths:

```bash
vidxp init \
  --ffmpeg /absolute/path/to/ffmpeg \
  --ffprobe /absolute/path/to/ffprobe
```

### 3. Download models

```bash
vidxp prepare
```

VidXP shows the missing models, download size, and storage location before
asking for confirmation. Download only selected features when preferred:

```bash
vidxp prepare --modalities scene
vidxp prepare --modalities dialogue,actor
vidxp prepare --modalities videoprism  # action search
vidxp prepare --modalities sound       # music and environmental sounds
```

For a noninteractive script, add `--yes`. Indexing and search commands do not
silently download missing models.

### 4. Verify the installation

```bash
vidxp doctor
```

When you installed only some search features, check only those features:

```bash
vidxp doctor --modalities scene
```

### 5. Import, index, and search a video

```bash
# Import a video and copy its media ID from the result
vidxp media import samplevideo.mp4 --json

# Index that media ID
vidxp index create <media-id>

# Find a visual scene
vidxp search scene "a yellow taxi on a city street"

# Find an action or event (`videoprism` is the CLI name for action search)
vidxp search videoprism "a person opens a door and walks outside"

# Find music or an environmental sound
vidxp search sound "an alarm ringing"

# Find something that was said
vidxp search dialogue "the bread just came out of the oven"
```

Add `--media-id <media-id>` to a search command to restrict results to one
video. Without it, VidXP searches all indexed videos in the active repository.

### Start an installed interface

| Interface | Command |
|---|---|
| Command help | `vidxp --help` |
| Browser app | `vidxp ui` |
| Local HTTP API and MCP server | `vidxp-api` |
| MCP server started as a local process | `vidxp-mcp` |

The browser app and API listen only on this computer by default. Their `--share`
options deliberately expose them to the local network. The browser share has no
authentication; the API share prints a bearer token. Use either only on a
trusted network.

## Connect a local AI assistant

Install the `local-worker,mcp` profile and prepare the models you want to use.
Then print the command and paths a local MCP client should use:

```bash
vidxp mcp-config
```

For Codex, add VidXP directly:

```bash
codex mcp add vidxp -- vidxp-mcp --repository default
codex mcp list
```

The ChatGPT desktop app, Codex CLI, and Codex IDE extension use the same MCP
configuration when they run on the same Codex host. ChatGPT on the web cannot
read this local configuration; it needs a deployed HTTPS VidXP server. See the
[official OpenAI MCP documentation](https://developers.openai.com/codex/mcp/)
for client setup details.

Check the VidXP MCP server itself with:

```bash
vidxp-mcp --check --repository default
```

## Use the local HTTP API

Install the `local-worker,server` profile, prepare models, and run:

```bash
vidxp-api
```

The default server is available only on this computer:

| Address | Purpose |
|---|---|
| `http://127.0.0.1:32191/docs` | Interactive API documentation |
| `http://127.0.0.1:32191/openapi.json` | Machine-readable API contract |
| `http://127.0.0.1:32191/health` | Service health |
| `http://127.0.0.1:32191/ready` | Runtime readiness |
| `http://127.0.0.1:32191/mcp` | Streamable HTTP MCP |

Use `vidxp-api --port <port>` when another local port is required.

Use `vidxp-api --share` only for a trusted local network. For HTTPS, hosted AI
clients, or public access, use the supported server deployment instead.

See [Local API and MCP server](docs/local-api.md) for authentication, uploads,
and sharing behavior.

## Optional dependency extras

Most users should choose one of the package profiles above. The individual
extras below are for developers embedding VidXP in a Python application or
assembling a custom installation:

| Extra | Adds |
|---|---|
| `dialogue` | Transcription, dialogue embeddings, and storage |
| `sound` | Music and environmental-sound search and storage |
| `scene` | Scene search and storage |
| `videoprism` | Action search and storage |
| `actor` | Actor matching and storage |
| `all` | Every built-in search feature |
| `local-worker` | All search features plus local job processing |
| `frontend` | Browser interface |
| `mcp` | Local stdio MCP |
| `server` | HTTP API and Streamable HTTP MCP control service |
| `server-worker` | CPU processing for a deployed server |
| `slm` | OpenAI-compatible grounded-answer model client |

The base `vidxp` package provides commands and shared contracts but cannot
index video by itself. Search features download their models only through the
explicit preparation step. Keep benchmark and test extras in contributor
environments rather than production applications.

## Docker

The published local image includes VidXP, CPU processing, the browser app,
Python, and FFmpeg.

Prepare models in a persistent volume:

```bash
docker run --rm -it \
  -v vidxp-data:/var/lib/vidxp \
  ghcr.io/grayhatdevelopers/vidxp:latest \
  vidxp prepare
```

Start VidXP:

```bash
docker run --rm --init \
  -p 8501:8501 \
  -v vidxp-data:/var/lib/vidxp \
  ghcr.io/grayhatdevelopers/vidxp:latest
```

Open `http://localhost:8501`. Pin a published version instead of `latest` for a
long-lived installation.

### Published images

Every stable release publishes these Linux x86-64 images from the same
validated release:

| Image | Tag | Purpose |
|---|---|---|
| Local | `<release>` or `latest` | CPU worker and browser interface |
| Control | `<release>-control` | API, remote MCP, migrations, and upload hooks |
| Worker | `<release>-worker` | CPU processing and server storage client |

All images use `ghcr.io/grayhatdevelopers/vidxp`. Pin an exact release for a
long-lived installation; the Coolify deployment uses the control and worker
images together.

## Self-hosted server

The supported server deployment runs on one machine and includes the VidXP API,
MCP, a CPU worker, PostgreSQL, Chroma, and resumable uploads. It requires HTTPS,
authentication, secrets, persistent storage, and backups.

Follow the [Coolify deployment guide](docs/deployment/coolify.md) for the full
operator procedure. The current deployment is single-node and single-repository;
it does not provide multi-node failover or GPU processing.

## After an upgrade

Run these checks after upgrading VidXP:

```bash
vidxp init
vidxp doctor
```

When an upgrade changes a search model or index format, existing videos may need
to be indexed again. VidXP reports this instead of silently replacing a working
index. Prepare the required models, re-index the affected videos, and keep the
old repository until you have checked the replacement results.

## Data locations

VidXP stores data outside the current working directory:

| Platform | Data | Configuration |
|---|---|---|
| Windows | `%LOCALAPPDATA%\VidXP` | `%APPDATA%\VidXP` |
| macOS | `~/Library/Application Support/VidXP` | `~/Library/Application Support/VidXP` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/VidXP` | `${XDG_CONFIG_HOME:-~/.config}/VidXP` |

The data location contains repositories, downloaded models, imported videos,
and generated results. The configuration location contains saved settings and
locally managed access tokens. Docker stores its persistent data in the
`vidxp-data` volume used in the commands above.

Use another location for one CLI command:

```bash
vidxp --data-dir /path/to/vidxp-data ui
```

Set `VIDXP_DATA_DIR` when every local VidXP interface should use the same
alternate location. `VIDXP_INDEX_DIR` and `VIDXP_MODEL_CACHE` override only the
repository or model location and are intended for advanced setups. Docker uses
its declared volumes instead.

## Troubleshooting

### `vidxp` is not found

```bash
uv tool update-shell
uv tool list
```

Restart the terminal after updating the shell.

### FFmpeg or a codec is missing

```bash
vidxp init
vidxp doctor
```

Review the suggested package-manager command before approving it.

### Linux or Windows starts downloading NVIDIA packages

Reinstall with the supported CPU dependency set:

```bash
uv tool install --force --python 3.14 --torch-backend cpu \
  "vidxp[local-worker,frontend]"
```

### uv warns that it cannot hardlink from its cache

uv falls back to copying files, so the installation remains valid. To suppress
the warning when the cache and installation intentionally use different
filesystems, reinstall with copy mode:

```bash
uv tool install --link-mode copy --python 3.14 --torch-backend cpu \
  "vidxp[local-worker,frontend]"
```

You can also set `UV_LINK_MODE=copy` for future uv commands.

### A model is missing

```bash
vidxp prepare
vidxp doctor
```

For later offline use, prepare models while online, then set
`VIDXP_ALLOW_MODEL_DOWNLOADS=false`.

### Search says the index is not ready

```bash
vidxp index status
vidxp jobs list
```

Inspect the failed job, fix the reported problem, and index the video again. A
failed or cancelled indexing job does not replace the last working index.

### Windows blocks the Desktop installer

Confirm that the installer came from the official GitHub release page, then use
Windows' option to open it. Do not bypass a warning for a package from another
source.

### Remove local data

VidXP does not provide a one-command destructive reset. First show the paths it
is using:

```bash
vidxp repositories show
```

Back up or remove only the specific repository or model directory you intend to
discard.

## Develop VidXP from source

Source setup is for contributors, not normal installation. Follow the
[contribution guide](docs/CONTRIBUTING.md).
