from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from vidxp.codex_plugin import (
    CodexPluginInstallError,
    export_codex_plugin,
    install_codex_plugin,
    resolve_codex_command,
)


def test_export_codex_plugin_materializes_skills_and_target_mcp_config() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory) / "codex-marketplace"
        index_directory = Path("C:/VidXP/repositories/default")
        data_directory = Path("C:/VidXP")
        exported = export_codex_plugin(
            root,
            repository="default",
            index_directory=str(index_directory),
            data_directory=data_directory,
        )

        plugin_root = root / "plugins" / "vidxp"
        manifest = json.loads(
            (plugin_root / ".codex-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        mcp = json.loads((plugin_root / ".mcp.json").read_text(encoding="utf-8"))
        marketplace_path = root / ".agents" / "plugins" / "marketplace.json"
        marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))

        assert manifest["name"] == "vidxp"
        assert manifest["version"] == exported.plugin_version
        assert "+codex." in exported.plugin_version
        assert (plugin_root / "skills" / "vidxp-ingest-video" / "SKILL.md").is_file()
        assert (
            plugin_root / "skills" / "vidxp-find-video-evidence" / "SKILL.md"
        ).is_file()
        assert mcp["mcpServers"]["vidxp"]["args"] == [
            "--repository",
            "default",
            "--index-directory",
            str(index_directory),
            "--data-dir",
            str(data_directory),
        ]
        assert Path(mcp["mcpServers"]["vidxp"]["command"]).name.lower() in {
            "vidxp-mcp",
            "vidxp-mcp.exe",
        }
        assert marketplace["name"] == "vidxp-local"
        assert marketplace["plugins"][0]["source"]["path"] == "./plugins/vidxp"
        assert marketplace["plugins"][0]["policy"] == {
            "installation": "AVAILABLE",
            "authentication": "ON_INSTALL",
        }
        assert exported.marketplace_path == str(marketplace_path)
        assert not (root / "marketplace.json").exists()


def test_export_migrates_the_legacy_managed_marketplace_layout() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory) / "marketplace"
        root.mkdir()
        (root / ".vidxp-managed-marketplace").write_text(
            "Managed by VidXP Desktop.\n",
            encoding="utf-8",
        )
        legacy_path = root / "marketplace.json"
        legacy_path.write_text("{}\n", encoding="utf-8")

        exported = export_codex_plugin(root)

        assert Path(exported.marketplace_path).is_file()
        assert not legacy_path.exists()


def test_install_codex_plugin_registers_marketplace_then_installs_bundle() -> None:
    calls: list[list[str]] = []

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[1:4] == ["plugin", "marketplace", "add"]:
            payload = {"marketplaceName": "vidxp-local", "alreadyAdded": False}
        else:
            payload = {
                "pluginId": "vidxp@vidxp-local",
                "name": "vidxp",
                "marketplaceName": "vidxp-local",
                "version": "0.4.0+codex.example",
                "installedPath": "/codex/cache/vidxp",
            }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    with TemporaryDirectory() as directory:
        result = install_codex_plugin(
            Path(directory) / "marketplace",
            codex_command="codex-test",
            runner=runner,
        )

    assert calls[0][0:4] == ["codex-test", "plugin", "marketplace", "add"]
    assert calls[0][-1] == "--json"
    assert calls[1] == [
        "codex-test",
        "plugin",
        "add",
        "vidxp@vidxp-local",
        "--json",
    ]
    assert result.plugin_id == "vidxp@vidxp-local"
    assert result.installed_path == "/codex/cache/vidxp"
    assert "MCP server and skills" in result.detail


def test_export_refuses_to_replace_an_unmanaged_marketplace() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory) / "marketplace"
        root.mkdir()
        (root / "keep.txt").write_text("user data", encoding="utf-8")

        with pytest.raises(CodexPluginInstallError, match="unmanaged marketplace"):
            export_codex_plugin(root)

        assert (root / "keep.txt").read_text(encoding="utf-8") == "user data"


def test_resolve_codex_command_prefers_desktop_configured_cli() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        codex_home = root / ".codex"
        configured = root / "current" / "codex.exe"
        stale = root / "OpenAI" / "Codex" / "bin" / "codex.exe"
        configured.parent.mkdir(parents=True)
        stale.parent.mkdir(parents=True)
        configured.touch()
        stale.touch()
        codex_home.mkdir()
        escaped_command = str(configured).replace("\\", "\\\\")
        (codex_home / "config.toml").write_text(
            "[mcp_servers.node_repl.env]\n"
            f'CODEX_CLI_PATH = "{escaped_command}"\n',
            encoding="utf-8",
        )

        resolved = resolve_codex_command(
            environment={
                "CODEX_HOME": str(codex_home),
                "LOCALAPPDATA": str(root),
            },
            which=lambda _: None,
        )

    assert resolved == str(configured)


def test_resolve_codex_command_uses_desktop_environment_path() -> None:
    with TemporaryDirectory() as directory:
        command = Path(directory) / "codex.exe"
        command.touch()

        resolved = resolve_codex_command(
            environment={"CODEX_CLI_PATH": str(command)},
            which=lambda _: None,
        )

    assert resolved == str(command)


def test_resolve_codex_command_falls_back_to_local_app_install() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        command = root / "OpenAI" / "Codex" / "bin" / "codex.exe"
        command.parent.mkdir(parents=True)
        command.touch()

        resolved = resolve_codex_command(
            environment={
                "CODEX_HOME": str(root / "missing-codex-home"),
                "LOCALAPPDATA": str(root),
            },
            which=lambda _: None,
        )

    assert resolved == str(command)


def test_resolve_codex_command_prefers_newest_versioned_local_cli() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        bin_directory = root / "OpenAI" / "Codex" / "bin"
        stable = bin_directory / "codex.exe"
        older = bin_directory / "old" / "codex.exe"
        current = bin_directory / "current" / "codex.exe"
        for command in (stable, older, current):
            command.parent.mkdir(parents=True, exist_ok=True)
            command.touch()
        os.utime(older, (1, 1))
        os.utime(current, (2, 2))

        resolved = resolve_codex_command(
            environment={
                "CODEX_HOME": str(root / "missing-codex-home"),
                "LOCALAPPDATA": str(root),
            },
            which=lambda _: None,
        )

    assert resolved == str(current)
