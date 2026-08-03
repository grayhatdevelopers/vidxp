## Download VidXP

{release_notice}VidXP turns video into searchable dialogue, scenes, people, and
inspectable evidence. Choose the desktop app for the guided local setup, or use
the Python package and containers for command-line and server deployments.

| Platform | Download |
| --- | --- |
| Windows x86-64 | [Installer]({windows_url}) |
| macOS Apple Silicon | [DMG]({macos_url}) |
| Linux x86-64 | [AppImage]({linux_url}) |

Verify downloaded files against [SHA256SUMS]({checksums_url}).

### Command line

```bash
python -m pip install "vidxp[local-worker,mcp]=={version}"
```

### Containers

```bash
docker pull {container_image}:{version}
```

Server deployments use the matching `-control` and `-worker` image tags.

[Installation guide]({installation_url}) · [Server deployment]({deployment_url}) ·
[Report an issue]({issues_url})
