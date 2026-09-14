from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from vidxp.local_probe import (  # noqa: E402
    desktop_capability_catalog,
    desktop_model_cache_catalog,
)


CATALOGS = {
    ROOT / "desktop" / "capability-catalog.json": desktop_capability_catalog,
    ROOT / "desktop" / "model-cache-catalog.json": desktop_model_cache_catalog,
}


def rendered_catalog(value: object) -> str:
    return json.dumps(
        value,
        indent=2,
        ensure_ascii=False,
    ) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    if arguments.write:
        for path, derive in CATALOGS.items():
            path.write_text(
                rendered_catalog(derive()),
                encoding="utf-8",
                newline="\n",
            )
        return 0
    stale = [
        path.relative_to(ROOT).as_posix()
        for path, derive in CATALOGS.items()
        if not path.exists()
        or path.read_text(encoding="utf-8") != rendered_catalog(derive())
    ]
    if stale:
        raise SystemExit(
            f"{', '.join(stale)} is stale; run npm run "
            "model-catalog:write from desktop/."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
