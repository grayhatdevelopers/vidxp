from __future__ import annotations

import base64
from functools import lru_cache
from importlib.resources import files
from pathlib import Path


PROJECT_URL = "https://github.com/grayhatdevelopers/vidxp"
ICON_MIME_TYPE = "image/png"
ICON_SIZE = "303x303"


def icon_path() -> str:
    """Return the canonical icon in source or its installed wheel copy."""

    packaged_icon = files("vidxp").joinpath("assets", "icon.png")
    if packaged_icon.is_file():
        return str(packaged_icon)

    source_icon = (
        Path(__file__).resolve().parents[2] / "docs" / "images" / "logo.png"
    )
    if source_icon.is_file():
        return str(source_icon)
    raise FileNotFoundError("VidXP icon is missing from the installation.")


@lru_cache(maxsize=1)
def icon_bytes() -> bytes:
    return Path(icon_path()).read_bytes()


@lru_cache(maxsize=1)
def icon_data_uri() -> str:
    encoded = base64.b64encode(icon_bytes()).decode("ascii")
    return f"data:{ICON_MIME_TYPE};base64,{encoded}"
