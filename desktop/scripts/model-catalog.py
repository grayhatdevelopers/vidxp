from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from vidxp.local_probe import (  # noqa: E402
    desktop_capability_catalog,
    desktop_model_cache_catalog,
)


GENERATED = ROOT / "desktop" / "generated"
CATALOGS = {
    GENERATED / "capability-catalog.json": desktop_capability_catalog,
    GENERATED / "model-cache-catalog.json": desktop_model_cache_catalog,
}


def rendered_catalog(value: object) -> str:
    return json.dumps(
        value,
        indent=2,
        ensure_ascii=False,
    ) + "\n"


def main() -> int:
    GENERATED.mkdir(parents=True, exist_ok=True)
    for path, derive in CATALOGS.items():
        path.write_text(
            rendered_catalog(derive()),
            encoding="utf-8",
            newline="\n",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
