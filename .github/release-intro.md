## Download VidXP

{release_notice}VidXP turns video into searchable dialogue, sounds, scenes, actions, people, and
inspectable evidence. Choose the desktop app for the guided local setup, or use
the Python package and containers for command-line and server deployments.

| Platform | Download |
| --- | --- |
| Windows x86-64 | [Installer]({windows_url}) |
| macOS Apple Silicon | [DMG]({macos_url}) |
| Linux x86-64 | [AppImage]({linux_url}) |

Beta and stable macOS DMGs are signed with a Developer ID certificate and
notarized by Apple. The Windows installer is not yet signed and may trigger
SmartScreen.

Verify downloaded files against [SHA256SUMS]({checksums_url}).

### Command line

```bash
uv tool install --python 3.14 --torch-backend cpu \
  "vidxp[local-worker,mcp]=={version}"
```

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) first. Add
`frontend` to the extras if you also want the local browser interface:

```bash
uv tool install --python 3.14 --torch-backend cpu \
  "vidxp[local-worker,mcp,frontend]=={version}"
```

### Containers

Browse published tags in the [VidXP container package]({container_package_url}).

```bash
docker pull {container_image}:{version}
```

Server deployments use the matching `-control` and `-worker` image tags.

[Installation guide]({installation_url}) · [Server deployment]({deployment_url}) ·
[Report an issue]({issues_url})
