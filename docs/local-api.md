# Use VidXP from another app

This guide is for developers and advanced users connecting an application or
AI assistant to VidXP. For ordinary Desktop, browser, or command-line use, see
the [installation guide](../INSTALLATION_GUIDE.md).

Choose the connection that matches the client:

| Client | Connection |
|---|---|
| An application that sends web requests | Start `vidxp-api` and use the HTTP API |
| An MCP client that connects to a web address | Start `vidxp-api` and use its `/mcp` address |
| An AI assistant that can start a local program | Configure it to run `vidxp-mcp` directly |

All three choices use the same VidXP repositories, models, jobs, and search
behavior.

## Connect through the local HTTP server

Install VidXP with local processing and server support:

```bash
uv tool install --python 3.14 --torch-backend cpu \
  "vidxp[local-worker,server]"
vidxp init
vidxp prepare
vidxp doctor
```

Start the server:

```bash
vidxp-api
```

By default, only applications on the same computer can connect. Open
`http://127.0.0.1:32191/docs` to browse the HTTP operations and try requests in
your browser.

Use these addresses when configuring an application:

| Address | Purpose |
|---|---|
| `http://127.0.0.1:32191` | HTTP API base address |
| `http://127.0.0.1:32191/mcp` | MCP connection address |
| `http://127.0.0.1:32191/health` | Confirms that the server is running |
| `http://127.0.0.1:32191/ready` | Confirms that required services are ready |

If port `32191` is already in use, choose another one:

```bash
vidxp-api --port 32192
```

### Ingest media already available on this computer

A local application can register media without copying the video through an
HTTP request. Send one to ten absolute file paths to
`POST /api/v1/media/local-ingestions`, then poll the URL from its `Location`
header until the session is terminal. Set `modalities` to the indexable names
returned by `GET /api/v1/capabilities`; omit it to use every enabled indexing
feature.

This operation is available only when `vidxp-api` runs in local mode. Every
path must be readable by the VidXP process and, when trusted import roots are
configured, must be inside one of those roots. The response never returns a
source path. Use the upload operations instead when the application and VidXP
do not share a filesystem.

The [Premiere Pro extension preview](integrations/premiere-pro.md) uses this
workflow for media already loaded in a Premiere project.

## Connect a local AI assistant

An assistant that can start a program on the same computer does not need the
HTTP server. Install VidXP with the `mcp` package extra, then print the command
and paths the assistant should use:

```bash
vidxp mcp-config --repository default
```

The generated configuration runs `vidxp-mcp` as a local process. See
[Connect VidXP to Codex or ChatGPT](integrations/openai-plugin.md) for Codex
setup and for the different requirements of hosted AI clients.

### Enable local grounded answers

Ordinary search and timestamped evidence do not require a language model.
`query_video` and `vidxp query` can additionally use a self-hosted Ollama model
to rewrite a question into searches and draft claims from citable textual
evidence. VidXP falls back to deterministic evidence retrieval when Ollama is
not configured or unavailable.

For VidXP Desktop, open **Setup options** and enable **Local grounded
answers**. Desktop first reuses a healthy Ollama service or existing executable.
On supported Desktop platforms, it otherwise asks before downloading the
pinned, checksum-verified headless runtime into VidXP's private data. It never
installs the Ollama desktop app. Desktop downloads the approved model with
visible progress and carries the non-secret local provider settings into every
managed surface, including copied stdio MCP JSON and **Set up in Codex**. If
Desktop starts `ollama serve`, it supervises and stops only that owned process.
It never stops an Ollama app or service that was already running.

The commands below are only for command-line installations and custom
deployments.

Install and start [Ollama](https://ollama.com/download), then explicitly
download VidXP's recommended model:

```bash
ollama pull qwen3.5:4b-q4_K_M
```

Set the Ollama OpenAI-compatible address before starting `vidxp-mcp`,
`vidxp-api`, or a CLI query. VidXP selects `qwen3.5:4b-q4_K_M` when the model
setting is omitted:

```bash
export VIDXP_SLM_BASE_URL=http://127.0.0.1:11434/v1
vidxp query "When does the taxi arrive?"
```

In PowerShell, set the same value with:

```powershell
$env:VIDXP_SLM_BASE_URL = "http://127.0.0.1:11434/v1"
vidxp query "When does the taxi arrive?"
```

The official Q4_K_M model download is approximately 3.4 GB. A Desktop-managed
headless runtime can add up to approximately 1.36 GiB; an existing service or
executable avoids that download. Local inference has no model API fee or
numbered hosted-model run, but it still uses local storage, memory, compute time,
and electricity. Desktop downloads the model only when the user selects the
feature; CLI users pull it explicitly. VidXP never bundles the model with the
Python or Desktop packages. A reused external Ollama service continues to own
its model storage; VidXP does not claim those files are in its search-model
cache.

The current query adapter sends structured search evidence, not video or audio
bytes, to Qwen. Speech transcripts can support generated factual claims.
Scene, action, and sound matches remain timestamped, inspectable retrieval
evidence until a later media-enrichment layer supplies citable descriptions.

## Add videos through MCP

The available method depends on how the MCP client connects.

### Client connected to `vidxp-api`

Call `create_media_upload` to receive a temporary upload link. Open the link in
a browser and choose the videos. Call `get_media_upload` to check importing and
indexing progress.

When the result contains `searchable=true`, the video is ready. VidXP normally
indexes every search feature enabled for the repository. A client can use the
`modalities` option to select fewer features.

The browser uploads each video directly to VidXP. Video data is never placed in
the MCP conversation or tool result. This local flow does not require Docker
or separately installed database and upload services.

### Client running `vidxp-mcp`

Call `ingest_local_media` with one to ten local file paths. Each file must be
inside a location VidXP permits for imports. Call `get_media_ingestion` to
check importing and indexing progress.

The file data stays on the computer and does not pass through MCP. As with an
upload, the client can use `modalities` to select a smaller set of search
features.

Use the `sound` modality to index or search music, environmental sounds, and
other non-speech audio events. CLI, HTTP, local stdio MCP, remote MCP, browser,
and Desktop all resolve that name through the same capability contract. Spoken
words use the `speech` capability, while multi-frame visible actions and motion
use `action`.

## Connect from another computer

To make the API reachable from another device on the same trusted network,
run:

```bash
vidxp-api --share
```

VidXP prints a network address and private access token. The other application
must send that token with every request. VidXP saves and reuses it, so treat it
as a password: keep it out of source control, screenshots, messages, and logs.

This mode uses ordinary HTTP rather than HTTPS. Use it only on a trusted
private network. Secure browser upload links are unavailable unless you also
configure an HTTPS upload address; VidXP reports that limitation when it
starts.

Never expose `vidxp-api --share` directly to the internet.

## Connect a hosted service

ChatGPT on the web and other hosted services cannot reach an address on your
computer or private network. They need a VidXP server at a public HTTPS address
with authentication, persistent storage, backups, and resumable uploads.

Follow [Deploy VidXP with Coolify](deployment/coolify.md) for the supported
server setup. That guide also covers the OIDC authentication used by hosted
clients that cannot send VidXP's private access token.
