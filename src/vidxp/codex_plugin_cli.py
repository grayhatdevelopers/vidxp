from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from vidxp.codex_plugin import CodexPluginInstallError, install_codex_plugin


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install VidXP's bundled MCP server and skills in Codex."
    )
    parser.add_argument(
        "--marketplace-root",
        type=Path,
        help="Dedicated local marketplace directory managed by VidXP Desktop.",
    )
    parser.add_argument(
        "--marketplace-source",
        help="Git marketplace source, such as owner/repository.",
    )
    parser.add_argument("--marketplace-ref", help="Git ref to fetch.")
    parser.add_argument(
        "--marketplace-sparse",
        action="append",
        default=[],
        help="Sparse checkout path for a Git marketplace; repeat as needed.",
    )
    parser.add_argument("--registry")
    parser.add_argument("--repository", default="default")
    parser.add_argument("--index-directory")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--device")
    return parser


def main(arguments: Sequence[str] | None = None) -> None:
    parser = _parser()
    options = parser.parse_args(arguments)
    try:
        result = install_codex_plugin(
            options.marketplace_root,
            marketplace_source=options.marketplace_source,
            marketplace_ref=options.marketplace_ref,
            marketplace_sparse=options.marketplace_sparse,
            registry=options.registry,
            repository=options.repository,
            index_directory=options.index_directory,
            data_directory=options.data_dir,
            device=options.device,
        )
    except (CodexPluginInstallError, OSError, ValueError) as exc:
        parser.exit(1, f"VidXP could not set up Codex: {exc}\n")
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
