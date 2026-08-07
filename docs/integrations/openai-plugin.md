# ChatGPT and Codex plugin integration

VidXP ships one versioned plugin from this repository. It provides the install,
ingest, and evidence-search skills, while an installed VidXP runtime provides
the local MCP server and its optional interactive MCP App view.

The sole editable plugin source lives at `plugins/vidxp/` and contains:

- `.codex-plugin/plugin.json`, the plugin manifest;
- `assets/logo.png`, used for the plugin logo and composer icon; and
- `skills/`, the canonical install, ingest, and evidence workflows.

The repository marketplace is declared at `.agents/plugins/marketplace.json`.
Other product code in the repository does not affect the marketplace: Codex
reads only that manifest and the plugin path it declares. In a repository
checkout the marketplace is discoverable as an available local marketplace; it
is not installed merely because the repository was opened. Packaging copies
the canonical plugin into the Python wheel at build time, and Release Please
keeps its manifest version aligned with the Python package.

## Install in Codex

### Ask Codex to install it

```text
Add https://github.com/grayhatdevelopers/vidxp as a Git plugin marketplace, install the VidXP plugin, then use its $vidxp-install skill to set up VidXP on this computer.
```

### VidXP Desktop button

With **AI assistant integration** enabled, VidXP Desktop shows two distinct
actions:

- **Set up in Codex** installs the plugin and registers the selected
  installation's absolute `vidxp-mcp` command, repository, and data paths, so
  Codex does not depend on its process `PATH`.
- **Copy MCP setup** retains the transport-only JSON flow for other compatible
  local MCP clients.

Signed beta builds register `grayhatdevelopers/vidxp` at `main`; signed stable
builds use `release`. Both use sparse checkout for `.agents/plugins` and
`plugins/vidxp`, so unrelated repository content is not downloaded into the
plugin cache. Development and pull-request builds export the packaged plugin to
the Desktop-private `vidxp-local` marketplace instead. The action uses the
documented `codex plugin marketplace add`, `codex plugin add`, and
`codex mcp add` commands. A successful Git install removes the obsolete managed
`vidxp-local` registration. Start a new Codex task after setup.

### Manual fallback

If an agent does not have an authorized local shell, add the beta marketplace
manually with:

```text
codex plugin marketplace add grayhatdevelopers/vidxp --ref main \
  --sparse .agents/plugins --sparse plugins/vidxp
codex plugin add vidxp@vidxp
```

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

The Git marketplace described above is for Codex's local plugin system. A
ChatGPT connection still requires a publicly reachable Streamable HTTP endpoint
(VidXP serves `/mcp`),
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
