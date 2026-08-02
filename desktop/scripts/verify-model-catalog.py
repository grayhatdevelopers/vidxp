from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from vidxp.local_probe import desktop_model_cache_catalog  # noqa: E402


catalog_path = ROOT / "desktop" / "model-cache-catalog.json"
committed = json.loads(catalog_path.read_text(encoding="utf-8"))
derived = desktop_model_cache_catalog()
if committed != derived:
    raise SystemExit(
        "desktop/model-cache-catalog.json is stale; regenerate it from "
        "vidxp.local_probe.desktop_model_cache_catalog()."
    )
