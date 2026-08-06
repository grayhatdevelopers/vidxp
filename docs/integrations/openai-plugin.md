# ChatGPT and Codex plugin integration

VidXP ships one versioned plugin bundle with its Python distribution. The
bundle combines the local MCP server definition with VidXP's canonical ingest
and evidence-search skills, while the MCP server exposes an optional interactive
view for hosts that implement MCP Apps.

The packaged bundle lives at
`src/vidxp/bundled_plugins/vidxp/` and contains:

- `.codex-plugin/plugin.json`, the plugin manifest;
- `.mcp.json`, which starts the installed `vidxp-mcp` command; and
- `skills/`, a release snapshot of the canonical root `skills/` folders.

The root skill folders remain the authoring source. Packaging tests require the
bundled snapshot to match their file inventory and text exactly, and Release
Please keeps the plugin manifest version aligned with the Python package.

## Install in Codex

With **AI assistant integration** enabled, VidXP Desktop shows two distinct
actions:

- **Set up in Codex** exports a target-specific copy of this bundle to a
  Desktop-managed `vidxp-local` marketplace, registers that marketplace through
  the Codex CLI, and installs the plugin. The generated `.mcp.json` pins the
  selected installation's absolute `vidxp-mcp` command, repository, and data
  paths, so Codex does not depend on its process `PATH`.
- **Copy MCP setup** retains the transport-only JSON flow for other compatible
  local MCP clients.

The Codex action installs the MCP server and both skills as one unit. It uses
the documented JSON forms of `codex plugin marketplace add` and
`codex plugin add`, leaves personal marketplace files and `config.toml`
untouched, and asks the user to start a new Codex chat after installation. The
exported marketplace lives in VidXP Desktop's private application-data
directory. Its catalog is written to the Codex marketplace contract at
`.agents/plugins/marketplace.json`, with the plugin at `plugins/vidxp/`. The
exporter also removes the obsolete root-level `marketplace.json` from earlier
VidXP-managed exports. The canonical distributable bundle remains checked into
this repository and packaged in every VidXP wheel.

## Interactive MCP App

`create_media_upload` and `get_job_evidence` advertise the same
`ui://vidxp/evidence-review-v1.html` resource. A compatible host can render it
inline to:

- open VidXP's short-lived HTTPS upload page and refresh session progress;
- review answer claims and annotated evidence-board pages;
- select up to ten ranked evidence IDs; and
- request exact keyframes or clips with `materialize_job_evidence` without
  rerunning retrieval.

The component completes the MCP Apps `ui/initialize` handshake, then uses the
standard `tools/call`, `ui/open-link`, `ui/request-display-mode`,
`ui/update-model-context`, and resize messages. ChatGPT-specific `window.openai`
helpers are feature-detected only as compatibility fallbacks and for ephemeral
widget state. VidXP remains authoritative for upload, job, search, and artifact
state, and non-UI clients receive the existing text, image, and resource-link
content.

The resource is self-contained, uses system fonts, has no remote script or
style dependencies, and publishes an explicit empty resource/connect CSP. Add
only exact HTTPS origins if future component assets or requests require them.

## Connect ChatGPT

The bundled `.mcp.json` is for local plugin hosts. A ChatGPT connection still
requires a publicly reachable Streamable HTTP endpoint (VidXP serves `/mcp`),
an HTTPS deployment or secure development tunnel, and registration in ChatGPT
Developer Mode.

Do not add a placeholder `.app.json`. That file can reference only the real app
identifier issued after the remote MCP connection is registered. Once that ID
exists, add the descriptor to the plugin bundle and validate the deployed app
through ChatGPT Developer Mode.

Before public submission, also complete the current OpenAI review requirements,
including organization verification, public endpoint availability, privacy and
support URLs, accurate tool metadata, and CSP validation.

## Official references

- [Plugin architecture](https://developers.openai.com/plugins/concepts/plugins)
- [Package a plugin](https://developers.openai.com/plugins/build/plugins)
- [Add a ChatGPT UI](https://developers.openai.com/plugins/build/chatgpt-ui)
- [UI guidelines](https://developers.openai.com/plugins/concepts/ui-guidelines)
- [Connect from ChatGPT](https://developers.openai.com/plugins/deploy/connect-chatgpt)
- [App review](https://developers.openai.com/plugins/deploy/app-review)
