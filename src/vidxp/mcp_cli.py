from __future__ import annotations

import argparse
from typing import Sequence

from vidxp.application_models import Principal
from vidxp.composition import (
    create_control_plane_application,
    create_local_application,
)
from vidxp.mcp import create_mcp_server


def main(arguments: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Run the local VidXP MCP server over stdio."
    )
    parser.add_argument("--registry")
    parser.add_argument("--repository")
    parser.add_argument("--index-directory")
    parser.add_argument("--device")
    options = parser.parse_args(arguments)

    local = create_local_application(
        registry_path=options.registry,
        repository_name=options.repository,
        index_directory=options.index_directory,
        device=options.device,
    )
    context = create_control_plane_application(local.settings)
    try:
        create_mcp_server(
            context,
            default_principal=Principal(
                subject="local",
                client_id="stdio",
                scopes=frozenset({"*"}),
            ),
        ).run("stdio")
    finally:
        context.close()
        local.close()


if __name__ == "__main__":
    main()
