# Connect VidXP to Codex or ChatGPT

VidXP lets an AI assistant search indexed videos and return evidence such as
timestamps, frames, boards, and clips.

Two pieces make the integration work:

- The **MCP server** gives the assistant access to VidXP tools and data.
- The optional **Codex plugin** adds guided workflows for setup, ingestion,
  indexing, search, and evidence review.

Choose a setup:

| What you want | Use |
|---|---|
| Connect Codex on the same computer | VidXP Desktop or the local `vidxp-mcp` server |
| Let Codex guide installation and video workflows | The Codex plugin plus the local MCP server |
| Connect ChatGPT on the web | A public HTTPS VidXP deployment |

## Check VidXP first

The simplest local setup is VidXP Desktop with **AI assistant integration**
enabled. For a command-line installation, install the `local-worker,mcp`
profile and run:

```bash
vidxp init
vidxp prepare
vidxp doctor
vidxp-mcp --check --repository default
```

The final command starts the MCP server, lists its tools, requests the current
index status, and prints the selected data and repository paths. If VidXP is
not ready, follow the [installation guide](../../INSTALLATION_GUIDE.md) before
connecting an assistant.

## Connect Codex from VidXP Desktop

1. Open VidXP Desktop and select the installation Codex should use.
2. Confirm that the installation passes its health check.
3. Select **Set up in Codex**.
4. Start a new Codex task.

Desktop installs the VidXP plugin and registers the selected installation's
exact `vidxp-mcp` executable, repository, data paths, and any Desktop-managed
local grounded-answer settings. It does not depend on that executable being
available on the shell's `PATH`, and a later Codex-launched stdio server does
not have to inherit environment variables from the Desktop process.

Select **Copy MCP setup** instead when another compatible local assistant needs
the MCP connection settings without the Codex plugin.

## Ask Codex to set everything up

Paste this request into Codex:

```text
Add https://github.com/grayhatdevelopers/vidxp as a Git plugin marketplace, install the VidXP plugin, then use its $vidxp-install skill to set up VidXP on this computer.
```

The plugin guides Codex through finding an existing installation or offering a
new Desktop or command-line setup. Codex should ask before it installs
software, changes an existing environment, or downloads models.

Start a new Codex task after setup so the task loads the plugin and VidXP MCP
tools.

## Connect Codex without the plugin

The plugin is optional. To connect only the VidXP tools, first print the local
MCP configuration:

```bash
vidxp mcp-config --repository default
```

Then register the server with Codex:

```bash
codex mcp add vidxp -- vidxp-mcp --repository default
codex mcp list
```

Use the absolute path to `vidxp-mcp` when it is not on `PATH`. If the selected
installation uses custom data, repository, or model locations, copy the full
command and environment printed by `vidxp mcp-config`. Do not reconstruct those
paths by hand.

Start a new Codex task after changing MCP configuration. The ChatGPT desktop
app, Codex CLI, and Codex IDE extension use the same configuration when they
run on the same Codex host.

Useful diagnostic commands are:

| Command | Result |
|---|---|
| `vidxp-mcp --check --repository default` | Starts the server and checks its tools and repository |
| `vidxp mcp-config --repository <name>` | Prints local-client JSON for a named repository |
| `vidxp-mcp --print-config` | Prints configuration JSON without other output |
| `vidxp-mcp --help` | Shows server options and a connection example |

## Connect ChatGPT on the web

ChatGPT on the web cannot start `vidxp-mcp` on your computer or read local
Codex configuration. Deploy VidXP at a public HTTPS address, then connect
ChatGPT to:

```text
https://your-vidxp-host.example/mcp
```

The deployment needs an identity provider that issues access tokens accepted
by VidXP. VidXP checks those tokens using its OIDC settings; it does not provide
user accounts or a login service itself.

Follow the [Coolify deployment guide](../deployment/coolify.md) to configure
HTTPS, authentication, storage, uploads, and the public MCP address. After the
deployment is ready, register it through ChatGPT Developer Mode.

## Use the interactive evidence view

Compatible MCP hosts can show VidXP's upload and evidence-review interface
inside the conversation. A user can:

- upload videos through a temporary secure link;
- monitor indexing progress;
- inspect the evidence supporting an answer; and
- request exact frames or clips without repeating the search.

The integration still works when a host cannot display this interface. Those
clients receive ordinary text, images, and resource links instead.

## Maintain the integration

This section is for contributors. The editable plugin is in `plugins/vidxp/`,
and `.agents/plugins/marketplace.json` publishes it from this repository.
Python packaging copies the same plugin into the wheel. Release Please keeps
the plugin version aligned with the Python package version.

Desktop release builds use the Git marketplace from `main` for beta and
`release` for stable. Development and pull-request builds use the packaged
plugin through Desktop's private `vidxp-local` marketplace. Successful release
setup removes that development registration.

Do not add a placeholder `.app.json`. Add the descriptor only after ChatGPT
issues the real application identifier for a registered remote connection.

Keep the MCP App resource self-contained. It must work without optional
ChatGPT compatibility helpers, use an explicit Content Security Policy, and
avoid remote scripts and styles. Hosts without MCP App support must continue to
receive the normal MCP results.

Before public submission, confirm the current OpenAI requirements for
organization verification, endpoint availability, privacy and support URLs,
tool metadata, authentication, and Content Security Policy.

Official references:

- [Codex MCP configuration](https://developers.openai.com/codex/mcp/)
- [Plugin architecture](https://developers.openai.com/plugins/concepts/plugins)
- [Package a plugin](https://developers.openai.com/plugins/build/plugins)
- [Add a ChatGPT UI](https://developers.openai.com/plugins/build/chatgpt-ui)
- [Connect from ChatGPT](https://developers.openai.com/plugins/deploy/connect-chatgpt)
- [App review](https://developers.openai.com/plugins/deploy/app-review)
