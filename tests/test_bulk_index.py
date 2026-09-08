import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from vidxp.application import VidXPApplication
from vidxp.application_models import (
    BulkIndexSkipReason,
    BulkIndexTargetState,
    InvalidRequestError,
    MediaAsset,
    MediaPage,
    PlanBulkIndexCommand,
)
from vidxp.capabilities.registry import create_capability_registry
from vidxp.core.media import MediaState, MediaStream
from vidxp.core.snapshots import GenerationReference, IndexSnapshot
from vidxp.runtime import ModelRuntime
from vidxp.repository_layout import RepositoryLayout
from vidxp.settings import VidXPSettings


FIRST_MEDIA_ID = "123456781234423481234567890abcde"
SECOND_MEDIA_ID = "223456781234423481234567890abcde"
GENERATION_ID = "323456781234423481234567890abcde"
SNAPSHOT_ID = "423456781234423481234567890abcde"
FIRST_SHA256 = "a" * 64
SECOND_SHA256 = "b" * 64
CONFIG_FINGERPRINT = "c" * 64
MANIFEST_SHA256 = "d" * 64


def media_asset(
    media_id: str,
    *,
    sha256: str = FIRST_SHA256,
    filename: str = "clip.mp4",
    state: MediaState = MediaState.ready,
) -> MediaAsset:
    return MediaAsset(
        media_id=media_id,
        video_id=media_id,
        original_filename=filename,
        sha256=sha256,
        byte_size=1024,
        detected_mime_type="video/mp4",
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration_seconds=3.0,
        streams=(MediaStream(index=0, kind="video", codec="h264"),),
        state=state,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def generation(
    media_id: str,
    *,
    input_sha256: str = FIRST_SHA256,
    modalities: tuple[str, ...] = ("scene",),
) -> GenerationReference:
    return GenerationReference(
        generation_id=GENERATION_ID,
        media_id=media_id,
        manifest_sha256=MANIFEST_SHA256,
        input_sha256=input_sha256,
        config_fingerprint=CONFIG_FINGERPRINT,
        modalities=modalities,
        record_counts={name: 1 for name in modalities},
        store_size_bytes_at_commit=2048,
    )


def snapshot(*references: GenerationReference) -> IndexSnapshot:
    return IndexSnapshot(
        snapshot_id=SNAPSHOT_ID,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        config_fingerprint=CONFIG_FINGERPRINT,
        configuration={"enabled_modalities": ["scene"]},
        generations={
            reference.media_id: reference for reference in references
        },
    )


class BulkIndexPlanTests(unittest.TestCase):
    def application(
        self,
        root: str | Path,
        *,
        assets: tuple[MediaAsset, ...],
        active_snapshot: IndexSnapshot | None = None,
        page_size: int | None = None,
    ) -> VidXPApplication:
        settings = VidXPSettings(
            repository_root=Path(root),
            runtime_backend="cpu",
        )
        media_service = Mock()
        by_id = {asset.media_id: asset for asset in assets}
        media_service.get.side_effect = lambda media_id: by_id[media_id]

        def list_media(command):
            if page_size is None:
                return MediaPage(items=assets, total=len(assets))
            start = 0 if command.cursor is None else int(command.cursor)
            window = assets[start : start + page_size]
            following = start + page_size
            return MediaPage(
                items=window,
                total=len(assets),
                next_cursor=(
                    str(following) if following < len(assets) else None
                ),
            )

        media_service.list.side_effect = list_media
        return VidXPApplication(
            settings=settings,
            layout=RepositoryLayout(root=Path(root)),
            registry=create_capability_registry(),
            runtime=ModelRuntime(settings),
            index_backend=Mock(),
            media=media_service,
            artifacts=Mock(),
            index_status=lambda: None,
            active_snapshot=lambda: active_snapshot,
        )

    def test_media_covered_by_the_active_snapshot_is_skipped(self):
        with TemporaryDirectory() as root:
            application = self.application(
                root,
                assets=(media_asset(FIRST_MEDIA_ID),),
                active_snapshot=snapshot(generation(FIRST_MEDIA_ID)),
            )
            plan = application.plan_bulk_index(
                PlanBulkIndexCommand(modalities=("scene",))
            )
        self.assertEqual(len(plan.skipped), 1)
        self.assertEqual(plan.pending, ())
        skipped = plan.skipped[0]
        self.assertEqual(skipped.reason, BulkIndexSkipReason.already_indexed)
        self.assertEqual(skipped.generation_id, GENERATION_ID)

    def test_media_missing_a_requested_modality_is_planned(self):
        with TemporaryDirectory() as root:
            application = self.application(
                root,
                assets=(media_asset(FIRST_MEDIA_ID),),
                active_snapshot=snapshot(
                    generation(FIRST_MEDIA_ID, modalities=("scene",))
                ),
            )
            plan = application.plan_bulk_index(
                PlanBulkIndexCommand(modalities=("scene", "speech"))
            )
        self.assertEqual(len(plan.pending), 1)
        self.assertEqual(plan.skipped, ())

    def test_replaced_media_content_is_planned_again(self):
        with TemporaryDirectory() as root:
            application = self.application(
                root,
                assets=(media_asset(FIRST_MEDIA_ID, sha256=SECOND_SHA256),),
                active_snapshot=snapshot(
                    generation(FIRST_MEDIA_ID, input_sha256=FIRST_SHA256)
                ),
            )
            plan = application.plan_bulk_index(
                PlanBulkIndexCommand(modalities=("scene",))
            )
        self.assertEqual(len(plan.pending), 1)
        self.assertEqual(plan.skipped, ())

    def test_reindex_plans_media_the_snapshot_already_covers(self):
        with TemporaryDirectory() as root:
            application = self.application(
                root,
                assets=(media_asset(FIRST_MEDIA_ID),),
                active_snapshot=snapshot(generation(FIRST_MEDIA_ID)),
            )
            plan = application.plan_bulk_index(
                PlanBulkIndexCommand(modalities=("scene",), reindex=True)
            )
        self.assertEqual(len(plan.pending), 1)
        self.assertEqual(plan.skipped, ())

    def test_media_that_is_not_ready_is_skipped(self):
        with TemporaryDirectory() as root:
            application = self.application(
                root,
                assets=(
                    media_asset(FIRST_MEDIA_ID, state=MediaState.pending),
                ),
            )
            plan = application.plan_bulk_index(
                PlanBulkIndexCommand(modalities=("scene",))
            )
        self.assertEqual(len(plan.skipped), 1)
        self.assertEqual(
            plan.skipped[0].reason,
            BulkIndexSkipReason.media_not_ready,
        )

    def test_an_empty_selection_covers_every_registered_media_item(self):
        with TemporaryDirectory() as root:
            application = self.application(
                root,
                assets=(
                    media_asset(FIRST_MEDIA_ID, filename="one.mp4"),
                    media_asset(SECOND_MEDIA_ID, filename="two.mp4"),
                ),
            )
            plan = application.plan_bulk_index(
                PlanBulkIndexCommand(modalities=("scene",))
            )
        self.assertEqual(len(plan.targets), 2)
        self.assertEqual(
            [target.state for target in plan.targets],
            [BulkIndexTargetState.pending] * 2,
        )

    def test_an_explicit_selection_ignores_other_media(self):
        with TemporaryDirectory() as root:
            application = self.application(
                root,
                assets=(
                    media_asset(FIRST_MEDIA_ID, filename="one.mp4"),
                    media_asset(SECOND_MEDIA_ID, filename="two.mp4"),
                ),
            )
            plan = application.plan_bulk_index(
                PlanBulkIndexCommand(
                    media_ids=(SECOND_MEDIA_ID,),
                    modalities=("scene",),
                )
            )
        self.assertEqual(len(plan.targets), 1)
        self.assertEqual(plan.targets[0].media_id, SECOND_MEDIA_ID)

    def test_every_page_of_registered_media_is_selected(self):
        with TemporaryDirectory() as root:
            application = self.application(
                root,
                assets=(
                    media_asset(FIRST_MEDIA_ID, filename="one.mp4"),
                    media_asset(SECOND_MEDIA_ID, filename="two.mp4"),
                ),
                page_size=1,
            )
            plan = application.plan_bulk_index(
                PlanBulkIndexCommand(modalities=("scene",))
            )
        self.assertEqual(
            [target.media_id for target in plan.targets],
            [FIRST_MEDIA_ID, SECOND_MEDIA_ID],
        )

    def test_an_unknown_capability_is_rejected(self):
        with TemporaryDirectory() as root:
            application = self.application(
                root,
                assets=(media_asset(FIRST_MEDIA_ID),),
            )
            with self.assertRaises(InvalidRequestError):
                application.plan_bulk_index(
                    PlanBulkIndexCommand(modalities=("nonexistent",))
                )

    def test_planning_reads_no_media_when_the_selection_is_rejected(self):
        with TemporaryDirectory() as root:
            application = self.application(
                root,
                assets=(media_asset(FIRST_MEDIA_ID),),
            )
            with self.assertRaises(InvalidRequestError):
                application.plan_bulk_index(
                    PlanBulkIndexCommand(modalities=("nonexistent",))
                )
            application.media.list.assert_not_called()


if __name__ == "__main__":
    unittest.main()
