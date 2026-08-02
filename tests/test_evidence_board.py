from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock

from vidxp.application_models import (
    Artifact,
    EvidenceBoardCandidate,
    EvidenceBoardJobRequest,
    EvidenceFrameMatch,
)
from vidxp.core.artifacts import ArtifactKind, ArtifactState
from vidxp.core.contracts import CancellationToken
from vidxp.evidence_board import EvidenceBoardService, plan_evidence_board_pages
from vidxp.infrastructure.pillow_board import PillowEvidenceBoardRenderer
from vidxp.ports import EvidenceBoardRenderTile
from vidxp.settings import VidXPSettings


MEDIA_ID = "123456781234423481234567890abcde"
GENERATION_ID = "223456781234423481234567890abcde"
JOB_ID = "323456781234423481234567890abcde"
SOURCE_FINGERPRINT = "a" * 64


def candidate(rank: int, *, media_id: str = MEDIA_ID) -> EvidenceBoardCandidate:
    return EvidenceBoardCandidate(
        evidence_id=f"{rank:064x}",
        rank=rank,
        media_id=media_id,
        generation_id=GENERATION_ID,
        modalities=("scene",),
        start=float(rank),
        end=float(rank + 1),
        representative_timestamp=float(rank) + 0.5,
        frame_match=EvidenceFrameMatch.representative,
        display_text=f"Result {rank}",
    )


def artifact(
    artifact_id: str,
    *,
    kind: ArtifactKind,
    mime_type: str,
) -> Artifact:
    return Artifact(
        artifact_id=artifact_id,
        media_id=MEDIA_ID,
        generation_id=GENERATION_ID,
        job_id=JOB_ID,
        kind=kind,
        profile="test",
        mime_type=mime_type,
        byte_size=100,
        sha256="b" * 64,
        state=ArtifactState.ready,
        created_at=datetime.now(timezone.utc),
    )


class EvidenceBoardPlanningTests(unittest.TestCase):
    def test_board_uses_pages_instead_of_a_ten_item_cap(self):
        pages, continuation = plan_evidence_board_pages(
            tuple(candidate(rank) for rank in range(1, 26)),
            tiles_per_page=24,
            pages_per_job=4,
        )

        self.assertEqual([len(page) for page in pages], [24, 1])
        self.assertIsNone(continuation)

    def test_board_returns_lossless_continuation_after_job_budget(self):
        pages, continuation = plan_evidence_board_pages(
            tuple(candidate(rank) for rank in range(1, 101)),
            tiles_per_page=24,
            pages_per_job=4,
        )

        self.assertEqual(sum(len(page) for page in pages), 96)
        self.assertEqual(continuation, 97)

    def test_pages_never_mix_media_or_skip_the_next_rank(self):
        second_media = "423456781234423481234567890abcde"
        values = tuple(
            candidate(rank, media_id=MEDIA_ID if rank % 2 else second_media)
            for rank in range(1, 7)
        )
        pages, continuation = plan_evidence_board_pages(
            values,
            tiles_per_page=24,
            pages_per_job=4,
        )

        self.assertEqual(len(pages), 4)
        self.assertTrue(
            all(len({item.media_id for item in page}) == 1 for page in pages)
        )
        self.assertEqual(continuation, 5)


class PillowEvidenceBoardRendererTests(unittest.TestCase):
    def test_renderer_composes_a_bounded_jpeg(self):
        try:
            from PIL import Image
        except ModuleNotFoundError:
            self.skipTest("Pillow is unavailable in this test profile")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.png"
            second = root / "second.png"
            destination = root / "board.jpg"
            Image.new("RGB", (640, 360), "red").save(first)
            Image.new("RGB", (320, 480), "blue").save(second)

            PillowEvidenceBoardRenderer().render(
                destination,
                title="Example video · page 1",
                tiles=(
                    EvidenceBoardRenderTile(
                        source=first,
                        annotation_lines=(
                            "#1 · 00:01.000 · scene",
                            "First",
                            "representative",
                        ),
                    ),
                    EvidenceBoardRenderTile(
                        source=second,
                        annotation_lines=(
                            "#2 · 00:02.000 · scene",
                            "Second",
                            "representative",
                        ),
                    ),
                ),
                columns=4,
                tile_width=320,
                tile_height=180,
                annotation_height=52,
                maximum_bytes=8 * 1024 * 1024,
                cancellation=CancellationToken(),
            )

            with Image.open(destination) as rendered:
                self.assertEqual(rendered.format, "JPEG")
                self.assertEqual(rendered.size, (640, 286))
            self.assertLess(destination.stat().st_size, 8 * 1024 * 1024)


class EvidenceBoardServiceTests(unittest.TestCase):
    def test_service_returns_board_page_and_partial_tile_map(self):
        artifacts = Mock()
        artifacts.create_evidence_frame.side_effect = [
            (
                artifact(
                    "523456781234423481234567890abcde",
                    kind=ArtifactKind.evidence_frame,
                    mime_type="image/png",
                ),
                640,
                360,
            ),
            RuntimeError("frame failed"),
        ]
        artifacts.create_evidence_board_page.return_value = (
            artifact(
                "623456781234423481234567890abcde",
                kind=ArtifactKind.evidence_board,
                mime_type="image/jpeg",
            ),
            640,
            286,
        )
        media = Mock()
        media.require_record.return_value = SimpleNamespace(
            duration_seconds=120.0,
            original_filename="example.mp4",
        )
        service = EvidenceBoardService(
            artifacts=artifacts,
            media=media,
            settings=VidXPSettings(),
        )
        request = EvidenceBoardJobRequest(
            source_job_id=JOB_ID,
            source_fingerprint=SOURCE_FINGERPRINT,
            candidates=(candidate(1), candidate(2)),
        )

        result = service.create(request)

        self.assertEqual(len(result.pages), 1)
        self.assertEqual(result.rendered_count, 1)
        self.assertEqual(result.failed_count, 1)
        self.assertEqual(
            result.tiles[0].keyframe_artifact_id, "523456781234423481234567890abcde"
        )
        self.assertIsNone(result.tiles[1].keyframe_artifact_id)
        self.assertEqual(
            result.pages[0].tile_ids, tuple(tile.tile_id for tile in result.tiles)
        )


if __name__ == "__main__":
    unittest.main()
