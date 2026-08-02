from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from vidxp.local_probe import desktop_model_cache_catalog  # noqa: E402


CATALOG_PATH = ROOT / "desktop" / "model-cache-catalog.json"


def rendered_catalog() -> str:
    return json.dumps(
        desktop_model_cache_catalog(),
        indent=2,
        ensure_ascii=False,
    ) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    derived = rendered_catalog()
    if arguments.write:
        CATALOG_PATH.write_text(derived, encoding="utf-8", newline="\n")
        return 0
    if CATALOG_PATH.read_text(encoding="utf-8") != derived:
        raise SystemExit(
            "desktop/model-cache-catalog.json is stale; run npm run "
            "model-catalog:write from desktop/."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
