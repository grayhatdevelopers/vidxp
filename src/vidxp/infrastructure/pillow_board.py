from __future__ import annotations

from math import ceil
from pathlib import Path
from typing import Sequence

from vidxp.core.artifacts import (
    ArtifactRenderError,
    ArtifactRendererUnavailableError,
)
from vidxp.core.contracts import CancellationToken
from vidxp.ports import EvidenceBoardRenderTile


class PillowEvidenceBoardRenderer:
    """Compose verified frame artifacts into one bounded JPEG overview page."""

    _BACKGROUND = (9, 13, 23)
    _TILE_BACKGROUND = (18, 24, 38)
    _TEXT = (242, 245, 250)
    _MUTED = (174, 183, 198)
    _ACCENT = (124, 92, 255)

    def render(
        self,
        destination: Path,
        *,
        title: str,
        tiles: Sequence[EvidenceBoardRenderTile],
        columns: int,
        tile_width: int,
        tile_height: int,
        annotation_height: int,
        maximum_bytes: int,
        cancellation: CancellationToken,
    ) -> None:
        try:
            from PIL import Image, ImageDraw, ImageFont, ImageOps
        except ModuleNotFoundError as exc:
            raise ArtifactRendererUnavailableError(
                "Pillow is required to render evidence boards."
            ) from exc

        if not tiles or columns <= 0:
            raise ArtifactRenderError("An evidence board page requires tiles.")
        active_columns = min(columns, len(tiles))
        rows = ceil(len(tiles) / active_columns)
        header_height = 54
        width = active_columns * tile_width
        height = header_height + rows * (tile_height + annotation_height)

        try:
            font = ImageFont.load_default(size=15)
            small_font = ImageFont.load_default(size=13)
        except TypeError:  # Pillow versions before scalable bundled fonts.
            font = ImageFont.load_default()
            small_font = font

        canvas = Image.new("RGB", (width, height), self._BACKGROUND)
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((0, 0, width, 3), fill=self._ACCENT)
        draw.text((14, 17), self._bounded(title, 120), font=font, fill=self._TEXT)

        for index, tile in enumerate(tiles):
            cancellation.raise_if_cancelled()
            row, column = divmod(index, active_columns)
            left = column * tile_width
            top = header_height + row * (tile_height + annotation_height)
            image_box = (left, top, left + tile_width, top + tile_height)
            draw.rectangle(image_box, fill=self._TILE_BACKGROUND)
            if tile.source is None:
                placeholder = tile.placeholder or "Frame unavailable"
                draw.text(
                    (left + 14, top + tile_height // 2 - 8),
                    self._bounded(placeholder, 42),
                    font=font,
                    fill=self._MUTED,
                )
            else:
                try:
                    with Image.open(tile.source) as source:
                        source.load()
                        fitted = ImageOps.fit(
                            source.convert("RGB"),
                            (tile_width, tile_height),
                            method=Image.Resampling.LANCZOS,
                        )
                        canvas.paste(fitted, (left, top))
                        fitted.close()
                except OSError as exc:
                    raise ArtifactRenderError(
                        "An evidence-board input frame could not be decoded."
                    ) from exc
            annotation_top = top + tile_height
            draw.rectangle(
                (
                    left,
                    annotation_top,
                    left + tile_width,
                    annotation_top + annotation_height,
                ),
                fill=self._TILE_BACKGROUND,
            )
            for line_index, line in enumerate(tile.annotation_lines[:3]):
                draw.text(
                    (left + 8, annotation_top + 5 + line_index * 14),
                    self._bounded(line, max(18, tile_width // 7)),
                    font=font if line_index == 0 else small_font,
                    fill=self._TEXT if line_index == 0 else self._MUTED,
                )

        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            for quality in (85, 78, 70):
                canvas.save(
                    destination,
                    format="JPEG",
                    quality=quality,
                    optimize=False,
                    progressive=False,
                    subsampling=2,
                )
                if destination.stat().st_size <= maximum_bytes:
                    return
        except OSError as exc:
            raise ArtifactRenderError(
                "The evidence board could not be encoded."
            ) from exc
        finally:
            canvas.close()
        raise ArtifactRenderError("The evidence board exceeds its encoded-byte budget.")

    @staticmethod
    def _bounded(value: str, limit: int) -> str:
        compact = " ".join(value.split())
        if len(compact) <= limit:
            return compact
        return compact[: max(1, limit - 1)].rstrip() + "…"
