---
name: vidxp-install
description: Install, update, or repair VidXP Desktop or the VidXP CLI and connect its local MCP server to Codex. Use when a user asks to install VidXP, choose between Desktop and CLI, enable local video search features, configure VidXP for an agent, or fix a missing VidXP runtime. This bootstrap skill does not require VidXP to already be installed.
---

# Install VidXP

Reuse a working local VidXP setup when the user approves it. Otherwise, help them choose a setup, install only what they approve, verify it, and connect Codex to its local MCP server.

## Check for an existing installation first

Before offering a new install, look for every `vidxp` executable available to the local shell. Do not modify any candidate. For each one, run:

```text
<absolute-vidxp-path> --version
<absolute-vidxp-path> desktop-probe --json --desktop-version codex-plugin --request-id codex-install
```

For each successful probe, show the user its executable, version, `data_root`, `repository_root`, and `model_root`. These are the installation's effective paths; existing downloaded models under the reported `model_root` can be reused. Ask the user to confirm whether to reuse that installation and those paths.

If the user approves reuse, skip installation and upgrades. Use executables from that same environment, preserve the reported paths when registering MCP, and repair a missing surface or dependency only with approval. If no compatible installation is found or the user declines reuse, continue with the setup choice.

## Start with the choice

Determine which surface the user wants before downloading anything:

- Recommend **Desktop** when they want a guided installer, managed runtime, feature selection, or browser interface.
- Recommend **CLI** when they want scripting, terminal control, automation, or a minimal agent-only setup.
- Clarify that both keep video processing local. The plugin itself is only the bootstrap and agent guidance; VidXP provides the actual MCP server after installation.
- A browser-only ChatGPT session cannot install native software. Continue only when the agent has an authorized local shell, or give the user the exact manual steps.

Ask before starting an installer, changing a tool environment, or downloading models. Do not download model weights until the user has chosen the search capabilities that need them.

## Desktop path

1. Identify the operating system and architecture.
2. Use the latest applicable release from `https://github.com/grayhatdevelopers/vidxp/releases`:
   - Windows x86-64: signed setup executable.
   - Apple Silicon macOS: signed and notarized DMG.
   - Linux x86-64: AppImage.
3. Prefer the stable release unless the user explicitly requests beta. Verify any published checksum before launching the artifact.
4. Let the user complete the native installer and choose the VidXP capabilities in Desktop. Do not silently select model-heavy features.
5. In VidXP Desktop, use **Set up Codex** after the runtime reports healthy. Desktop registers this plugin and the exact private-runtime `vidxp-mcp` command.
6. Start a new Codex task, then verify the VidXP MCP tools are available.

## CLI path

1. Verify that `uv` is installed from `https://docs.astral.sh/uv/getting-started/installation/`.
2. Install the CPU edition with MCP support:

   ```text
   uv tool install --python 3.14 --torch-backend cpu "vidxp[local-worker,mcp]"
   ```

   Add `frontend` to the extras only when the user wants the browser interface.
3. Initialize runtime dependencies:

   ```text
   vidxp init
   ```

4. Run `vidxp prepare` only after the user approves the required model downloads. Prefer capability-specific preparation when their choice is narrower than the default set.
5. Validate the installation:

   ```text
   vidxp doctor
   ```

6. Resolve the installed `vidxp-mcp` executable to an absolute path. Register it through the supported Codex CLI instead of editing configuration files by hand:

   ```text
   codex mcp add vidxp -- <absolute-vidxp-mcp-path> --repository default
   ```

   When reusing an installation, also pass its reported `data_root` as `--data-dir` and a non-default `repository_root` as `--index-directory`. If `model_root` is not `<data_root>/models`, add `--env VIDXP_MODEL_CACHE=<model_root>` before `vidxp` so Codex launches MCP against the same model cache.

7. Run `codex mcp get vidxp --json` and start a new Codex task before testing VidXP tools.

## Updates and repairs

- Desktop: use a current installer for the same channel, then re-run **Set up Codex** so the private runtime path and plugin source are refreshed.
- CLI: use `uv tool upgrade vidxp`, run `vidxp doctor`, and re-register the resolved `vidxp-mcp` command if its path changed.
- If Codex reports duplicate VidXP plugins, keep the Git-backed `vidxp` marketplace for release installs and remove the obsolete `vidxp@vidxp-local` entry only after the Git-backed plugin works.

## Finish with evidence

Report the selected surface and release channel, exact installer or command used, `vidxp doctor` result, registered MCP command path, and whether a new Codex task can see the VidXP tools. Never call an unverified installation successful.
