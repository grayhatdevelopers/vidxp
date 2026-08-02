from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Sequence


def mcp_executable() -> str:
    """Return the most reliable executable path for desktop MCP clients."""

    discovered = shutil.which("vidxp-mcp")
    if discovered is not None:
        return str(Path(discovered).resolve())
    executable_name = "vidxp-mcp.exe" if os.name == "nt" else "vidxp-mcp"
    sibling = Path(sys.executable).with_name(executable_name)
    if sibling.is_file():
        return str(sibling.resolve())
    return "vidxp-mcp"


def stdio_client_config(
    *,
    command: str | None = None,
    registry: str | None = None,
    repository: str | None = "default",
    index_directory: str | None = None,
    data_directory: Path | None = None,
    device: str | None = None,
) -> dict[str, Any]:
    """Build Claude Desktop/compatible stdio ``mcpServers`` JSON."""

    arguments: list[str] = []
    for flag, value in (
        ("--registry", registry),
        ("--repository", repository),
        ("--index-directory", index_directory),
        ("--data-dir", data_directory),
        ("--device", device),
    ):
        if value is not None:
            arguments.extend((flag, str(value)))
    return {
        "mcpServers": {
            "vidxp": {
                "command": command or mcp_executable(),
                "args": arguments,
            }
        }
    }


def render_stdio_client_config(**options: Any) -> str:
    return json.dumps(
        stdio_client_config(**options),
        ensure_ascii=False,
        indent=2,
    )


def _parser() -> argparse.ArgumentParser:
    example = render_stdio_client_config()
    return argparse.ArgumentParser(
        description="Run the local VidXP MCP server over stdio.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "CLAUDE DESKTOP / COMPATIBLE STDIO CONFIG\n"
            "Add this JSON to a client that supports the mcpServers format:\n\n"
            f"{example}\n\n"
            "Codex: run `codex mcp add vidxp -- vidxp-mcp --repository default`, "
            "or configure [mcp_servers.vidxp] in ~/.codex/config.toml. "
            "ChatGPT web connects to a hosted HTTPS /mcp endpoint, not this "
            "local JSON. Run `vidxp-mcp --print-config` to print only the JSON."
        ),
    )


async def _inspect_server(server: Any) -> dict[str, Any]:
    from mcp.client import Client

    async with Client(server) as client:
        discovered = await client.list_tools()
        status = await client.call_tool("get_index_status", {})
        if status.is_error:
            raise RuntimeError("The MCP index-status probe failed.")
        server_info = client.server_info
    return {
        "server": server_info.title or server_info.name,
        "version": server_info.version,
        "tools": [tool.name for tool in discovered.tools],
        "index_state": (status.structured_content or {}).get(
            "state",
            "unknown",
        ),
    }


def main(arguments: Sequence[str] | None = None) -> None:
    parser = _parser()
    parser.add_argument(
        "--registry",
        help="Path to the named-repository configuration file.",
    )
    parser.add_argument(
        "--repository",
        help="Named repository to use; the implicit default is 'default'.",
    )
    parser.add_argument(
        "--index-directory",
        help="Override the selected repository's index directory.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="Store VidXP models and the default repository here.",
    )
    parser.add_argument(
        "--device",
        help="Override the selected repository runtime device.",
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--print-config",
        action="store_true",
        help="Print Claude Desktop/compatible mcpServers JSON and exit.",
    )
    actions.add_argument(
        "--check",
        action="store_true",
        help=(
            "Perform a local MCP handshake and tool probe, print the resolved "
            "runtime paths, and exit."
        ),
    )
    options = parser.parse_args(arguments)
    if options.print_config:
        print(
            render_stdio_client_config(
                registry=options.registry,
                repository=options.repository or "default",
                index_directory=options.index_directory,
                data_directory=options.data_dir,
                device=options.device,
            )
        )
        return

    from vidxp.application_models import Principal
    from vidxp.composition import (
        create_control_plane_application,
        create_local_application,
    )
    try:
        from vidxp.mcp import create_mcp_server
    except ModuleNotFoundError as exc:
        if exc.name == "mcp":
            parser.error(
                'MCP support is not installed. Install "vidxp[mcp]" in this '
                "same environment, then run the command again."
            )
        raise

    local = create_local_application(
        registry_path=options.registry,
        repository_name=options.repository,
        index_directory=options.index_directory,
        data_directory=options.data_dir,
        device=options.device,
    )
    context = create_control_plane_application(local.settings)
    try:
        server = create_mcp_server(
            context,
            default_principal=Principal(
                subject="local",
                client_id="stdio",
                scopes=frozenset({"*"}),
            ),
            filesystem_accessible=(context.settings.mcp_stdio_filesystem_accessible),
        )
        if options.check:
            result = asyncio.run(_inspect_server(server))
            print(f"OK {result['server']} MCP {result['version']}")
            print(f"Repository: {local.repository.name}")
            print(f"Data: {context.settings.data_dir}")
            print(f"Index: {context.settings.repository_root}")
            print(f"Index state: {result['index_state']}")
            print(
                f"Tools: {len(result['tools'])} "
                f"({', '.join(result['tools'])})"
            )
            return
        server.run("stdio")
    finally:
        context.close()
        local.close()


if __name__ == "__main__":
    main()
