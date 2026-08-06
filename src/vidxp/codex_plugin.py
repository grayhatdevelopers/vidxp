from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from vidxp import __version__
from vidxp.mcp_cli import stdio_client_config


PLUGIN_NAME = "vidxp"
MARKETPLACE_NAME = "vidxp-local"
MANAGED_MARKER = ".vidxp-managed-marketplace"
MARKETPLACE_MANIFEST = Path(".agents") / "plugins" / "marketplace.json"


class CodexPluginInstallError(RuntimeError):
    """Raised when the bundled Codex plugin cannot be installed safely."""


@dataclass(frozen=True)
class CodexPluginExport:
    marketplace_root: str
    marketplace_path: str
    marketplace_name: str
    plugin_name: str
    plugin_version: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class CodexPluginInstall:
    plugin_name: str
    plugin_id: str | None
    plugin_version: str
    marketplace_name: str
    marketplace_path: str
    installed_path: str | None
    detail: str

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


def bundled_codex_plugin() -> Path:
    return Path(__file__).resolve().parent / "bundled_plugins" / PLUGIN_NAME


def _json_bytes(payload: Any) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _write_json(path: Path, payload: Any) -> None:
    path.write_bytes(_json_bytes(payload))


def _bundle_digest(source: Path, mcp_config: dict[str, Any]) -> str:
    digest = hashlib.sha256(_json_bytes(mcp_config))
    for path in sorted(item for item in source.rglob("*") if item.is_file()):
        digest.update(path.relative_to(source).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def _validated_marketplace_root(marketplace_root: Path) -> Path:
    root = marketplace_root.expanduser().resolve()
    if root == Path(root.anchor):
        raise CodexPluginInstallError(
            "The Codex marketplace cannot be written at a filesystem root."
        )
    marker = root / MANAGED_MARKER
    if root.exists() and any(root.iterdir()) and not marker.is_file():
        raise CodexPluginInstallError(
            f"Refusing to replace the unmanaged marketplace directory at {root}."
        )
    return root


def export_codex_plugin(
    marketplace_root: Path,
    *,
    registry: str | None = None,
    repository: str = "default",
    index_directory: str | None = None,
    data_directory: Path | None = None,
    device: str | None = None,
) -> CodexPluginExport:
    """Export a target-specific copy of VidXP's bundled Codex plugin."""

    source = bundled_codex_plugin()
    if not (source / ".codex-plugin" / "plugin.json").is_file():
        raise CodexPluginInstallError(
            "This VidXP installation does not contain the bundled Codex plugin."
        )
    root = _validated_marketplace_root(marketplace_root)
    plugins_root = root / "plugins"
    plugins_root.mkdir(parents=True, exist_ok=True)
    plugin_root = plugins_root / PLUGIN_NAME
    marker = root / MANAGED_MARKER

    mcp_config = stdio_client_config(
        registry=registry,
        repository=repository,
        index_directory=index_directory,
        data_directory=data_directory,
        device=device,
    )
    plugin_version = (
        f"{__version__.split('+', 1)[0]}+codex."
        f"{_bundle_digest(source, mcp_config)}"
    )

    staging_parent = Path(tempfile.mkdtemp(prefix=".vidxp-plugin-", dir=plugins_root))
    staging_plugin = staging_parent / PLUGIN_NAME
    backup = plugins_root / ".vidxp-plugin-backup"
    try:
        shutil.copytree(source, staging_plugin)
        manifest_path = staging_plugin / ".codex-plugin" / "plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["version"] = plugin_version
        _write_json(manifest_path, manifest)
        _write_json(staging_plugin / ".mcp.json", mcp_config)

        if backup.exists():
            shutil.rmtree(backup)
        if plugin_root.exists():
            if not marker.is_file():
                raise CodexPluginInstallError(
                    f"Refusing to replace the unmanaged plugin at {plugin_root}."
                )
            os.replace(plugin_root, backup)
        try:
            os.replace(staging_plugin, plugin_root)
        except Exception:
            if backup.exists() and not plugin_root.exists():
                os.replace(backup, plugin_root)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if staging_parent.exists():
            shutil.rmtree(staging_parent)

    marketplace = {
        "name": MARKETPLACE_NAME,
        "interface": {"displayName": "VidXP Local"},
        "plugins": [
            {
                "name": PLUGIN_NAME,
                "source": {
                    "source": "local",
                    "path": f"./plugins/{PLUGIN_NAME}",
                },
                "policy": {
                    "installation": "AVAILABLE",
                    "authentication": "ON_INSTALL",
                },
                "category": "Productivity",
            }
        ],
    }
    marketplace_path = root / MARKETPLACE_MANIFEST
    marketplace_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(marketplace_path, marketplace)
    legacy_marketplace_path = root / "marketplace.json"
    if marker.is_file() and legacy_marketplace_path.is_file():
        legacy_marketplace_path.unlink()
    marker.write_text("Managed by VidXP Desktop.\n", encoding="utf-8")
    return CodexPluginExport(
        marketplace_root=str(root),
        marketplace_path=str(marketplace_path),
        marketplace_name=MARKETPLACE_NAME,
        plugin_name=PLUGIN_NAME,
        plugin_version=plugin_version,
    )


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def _run_codex_json(
    command: str,
    arguments: Sequence[str],
    *,
    runner: CommandRunner,
) -> dict[str, Any]:
    try:
        completed = runner(
            [command, *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CodexPluginInstallError(f"Codex could not be started: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise CodexPluginInstallError(
            detail or f"Codex exited with status {completed.returncode}."
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise CodexPluginInstallError(
            "Codex did not return valid plugin installation details."
        ) from exc
    if not isinstance(payload, dict):
        raise CodexPluginInstallError(
            "Codex returned an unexpected plugin installation response."
        )
    return payload


def _configured_codex_command(
    environment: Mapping[str, str],
) -> str | None:
    codex_home = Path(
        environment.get("CODEX_HOME", str(Path.home() / ".codex"))
    ).expanduser()
    try:
        config = tomllib.loads(
            (codex_home / "config.toml").read_text(encoding="utf-8")
        )
    except (OSError, tomllib.TOMLDecodeError):
        return None
    servers = config.get("mcp_servers")
    node_repl = servers.get("node_repl") if isinstance(servers, dict) else None
    node_repl_environment = (
        node_repl.get("env") if isinstance(node_repl, dict) else None
    )
    policy = config.get("shell_environment_policy")
    policy_environment = policy.get("set") if isinstance(policy, dict) else None
    for configured in (node_repl_environment, policy_environment):
        command = (
            configured.get("CODEX_CLI_PATH")
            if isinstance(configured, dict)
            else None
        )
        if isinstance(command, str) and command.strip():
            return command
    return None


def resolve_codex_command(
    *,
    environment: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> str | None:
    """Locate the current CLI bundled with Codex or available on PATH."""
    current_environment = os.environ if environment is None else environment
    candidates: list[str] = []
    environment_command = current_environment.get("CODEX_CLI_PATH")
    if environment_command:
        candidates.append(environment_command)
    configured_command = _configured_codex_command(current_environment)
    if configured_command:
        candidates.append(configured_command)

    local_app_data = current_environment.get("LOCALAPPDATA")
    if local_app_data:
        bin_directory = Path(local_app_data) / "OpenAI" / "Codex" / "bin"
        if bin_directory.is_dir():
            versioned = sorted(
                bin_directory.glob("*/codex.exe"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            candidates.extend(str(path) for path in versioned)
        candidates.append(str(bin_directory / "codex.exe"))

    path_command = which("codex")
    if path_command:
        candidates.append(path_command)

    seen: set[str] = set()
    for candidate in candidates:
        normalized = os.path.normcase(os.path.abspath(os.path.expanduser(candidate)))
        if normalized in seen:
            continue
        seen.add(normalized)
        if Path(candidate).expanduser().is_file():
            return str(Path(candidate).expanduser())
    return None


def install_codex_plugin(
    marketplace_root: Path,
    *,
    registry: str | None = None,
    repository: str = "default",
    index_directory: str | None = None,
    data_directory: Path | None = None,
    device: str | None = None,
    codex_command: str | None = None,
    runner: CommandRunner = subprocess.run,
) -> CodexPluginInstall:
    """Export, register, and install VidXP's local Codex plugin."""

    exported = export_codex_plugin(
        marketplace_root,
        registry=registry,
        repository=repository,
        index_directory=index_directory,
        data_directory=data_directory,
        device=device,
    )
    command = codex_command or resolve_codex_command()
    if command is None:
        raise CodexPluginInstallError(
            "The Codex CLI was not found. Install or update the ChatGPT desktop "
            "app, make the codex command available, and try again."
        )

    marketplace_result = _run_codex_json(
        command,
        [
            "plugin",
            "marketplace",
            "add",
            exported.marketplace_root,
            "--json",
        ],
        runner=runner,
    )
    marketplace_name = str(
        marketplace_result.get("marketplaceName") or exported.marketplace_name
    )
    plugin_result = _run_codex_json(
        command,
        [
            "plugin",
            "add",
            f"{exported.plugin_name}@{marketplace_name}",
            "--json",
        ],
        runner=runner,
    )
    return CodexPluginInstall(
        plugin_name=str(plugin_result.get("name") or exported.plugin_name),
        plugin_id=(
            None
            if plugin_result.get("pluginId") is None
            else str(plugin_result["pluginId"])
        ),
        plugin_version=str(
            plugin_result.get("version") or exported.plugin_version
        ),
        marketplace_name=str(
            plugin_result.get("marketplaceName") or marketplace_name
        ),
        marketplace_path=exported.marketplace_path,
        installed_path=(
            None
            if plugin_result.get("installedPath") is None
            else str(plugin_result["installedPath"])
        ),
        detail=(
            "VidXP is installed in Codex with its MCP server and skills. "
            "Start a new Codex chat to use the updated plugin."
        ),
    )
