from __future__ import annotations

from pathlib import Path


def detect_shot_boundaries(path: str | Path) -> list[float]:
    """Return sorted shot-end timestamps (seconds) using PySceneDetect.

    An empty list means no cuts were found (or detection failed) -
    callers should treat that as "one shot covering the whole video".
    """
    from scenedetect import ContentDetector, detect

    scene_list = detect(str(path), ContentDetector())
    return [end.get_seconds() for _start, end in scene_list]
