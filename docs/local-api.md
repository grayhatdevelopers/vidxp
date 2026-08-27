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
words remain a separate `dialogue` modality, while multi-frame visible actions
use `videoprism`.

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
