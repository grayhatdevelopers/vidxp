import hashlib
import shutil
import subprocess
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock

from vidxp.application_models import (
    ApplicationError,
    Artifact,
    EvidenceDeliveryMode,
    EvidenceDeliveryPolicy,
    EvidenceDeliveryState,
    EvidenceFrameMatch,
    FusedSearchResult,
    FusionProvenance,
    MomentEvidence,
    ImportMediaCommand,
    QueryAnswer,
    QueryAnswerMode,
    QueryPlan,
    SearchMomentsPlanStep,
    SearchHit,
    SearchResult,
)
from vidxp.core.artifacts import ArtifactKind, ArtifactState
from vidxp.core.contracts import CancellationToken
from vidxp.evidence_delivery import (
    EvidenceDeliveryService,
    resolve_evidence_range,
)
from vidxp.artifact_service import ArtifactService
from vidxp.execution import ExecutionContext
from vidxp.infrastructure.local_artifacts import FFmpegFrameRenderer
from vidxp.infrastructure.local_artifacts import (
    FFmpegSnippetRenderer,
    LocalArtifactStore,
)
from vidxp.infrastructure.local_catalog import LocalCatalog
from vidxp.infrastructure.local_media import FFprobeMediaProbe, LocalMediaStore
from vidxp.media_service import MediaService
from vidxp.search_fusion import fuse_search_results
from vidxp.settings import VidXPSettings


MEDIA_ID = "123456781234423481234567890abcde"
GENERATION_ID = "223456781234423481234567890abcde"
JOB_ID = "323456781234423481234567890abcde"
SNAPSHOT_ID = "423456781234423481234567890abcde"


def scene_hit(*, rank: int = 1, generation_id: str = GENERATION_ID) -> SearchHit:
    return SearchHit(
        rank=rank,
        media_id=MEDIA_ID,
        video_id=MEDIA_ID,
        generation_id=generation_id,
        start=1.0,
        end=2.0,
        score=0.9,
        raw_distance=0.1,
        modality="scene",
        source_id=f"scene:frame:{rank}",
        metadata={"frame_index": 7, "timestamp": 1.4, "fps": 5.0},
    )


def fused(*, snapshot_id: str = SNAPSHOT_ID) -> FusedSearchResult:
    atomic = SearchResult(
        query_id="scene:known",
        query="green frame",
        modality="scene",
        hits=(scene_hit(),),
    )
    return fuse_search_results(
        query="green frame",
        requested_modalities=("scene",),
        results=(atomic,),
        snapshot_id=snapshot_id,
    )


def artifact(kind: ArtifactKind, artifact_id: str, mime_type: str) -> Artifact:
    return Artifact(
        artifact_id=artifact_id,
        media_id=MEDIA_ID,
        generation_id=(GENERATION_ID if kind == ArtifactKind.evidence_frame else None),
        job_id=JOB_ID,
        kind=kind,
        profile="png" if kind == ArtifactKind.evidence_frame else "compatible_mp4",
        mime_type=mime_type,
        byte_size=123,
        sha256=("a" if kind == ArtifactKind.evidence_frame else "b") * 64,
        state=ArtifactState.ready,
        created_at=datetime.now(timezone.utc),
    )


class EvidenceIdentityTests(unittest.TestCase):
    def test_moment_identity_is_stable_and_snapshot_bound(self):
        first = fused()
        repeated = fused()
        changed = fused(snapshot_id="523456781234423481234567890abcde")

        self.assertEqual(first.moments[0].moment_id, repeated.moments[0].moment_id)
        self.assertNotEqual(first.moments[0].moment_id, changed.moments[0].moment_id)
        self.assertEqual(first.moments[0].hits[0].source_id, "scene:frame:1")

    def test_moment_identity_is_not_rank_only(self):
        first = fused()
        changed_hit = scene_hit().model_copy(update={"source_id": "scene:other"})
        second = fuse_search_results(
            query="green frame",
            requested_modalities=("scene",),
            results=(
                SearchResult(
                    query_id="scene:known",
                    query="green frame",
                    modality="scene",
                    hits=(changed_hit,),
                ),
            ),
            snapshot_id=SNAPSHOT_ID,
        )
        self.assertEqual(first.moments[0].rank, second.moments[0].rank)
        self.assertNotEqual(first.moments[0].moment_id, second.moments[0].moment_id)

    def test_changed_generation_changes_moment_identity(self):
        first = fused()
        changed_hit = scene_hit(
            generation_id="723456781234423481234567890abcde"
        )
        changed = fuse_search_results(
            query="green frame",
            requested_modalities=("scene",),
            results=(
                SearchResult(
                    query_id="scene:known",
                    query="green frame",
                    modality="scene",
                    hits=(changed_hit,),
                ),
            ),
            snapshot_id=SNAPSHOT_ID,
        )
        self.assertNotEqual(
            first.moments[0].moment_id,
            changed.moments[0].moment_id,
        )


class EvidenceRangeTests(unittest.TestCase):
    def resolve(self, **changes):
        values = {
            "source_start": 4.0,
            "source_end": 6.0,
            "representative_timestamp": 5.0,
            "media_duration": 10.0,
            "padding_before": 2.0,
            "padding_after": 2.0,
            "max_duration": 5.0,
        }
        values.update(changes)
        return resolve_evidence_range(**values)

    def test_clamps_at_zero_and_media_end(self):
        beginning = self.resolve(
            source_start=0.1,
            source_end=0.5,
            representative_timestamp=0.3,
        )
        ending = self.resolve(
            source_start=9.5,
            source_end=10.0,
            representative_timestamp=9.75,
        )
        self.assertEqual(beginning.clip_start_seconds, 0)
        self.assertTrue(beginning.start_clamped)
        self.assertEqual(ending.clip_end_seconds, 10)
        self.assertTrue(ending.end_clamped)

    def test_long_interval_is_centered_and_truncated(self):
        result = self.resolve(
            source_start=1,
            source_end=9,
            representative_timestamp=6,
            padding_before=0,
            padding_after=0,
        )
        self.assertEqual(result.clip_start_seconds, 3.5)
        self.assertEqual(result.clip_end_seconds, 8.5)
        self.assertTrue(result.source_interval_truncated)

    def test_reports_requested_and_applied_padding(self):
        result = self.resolve(
            source_start=4,
            source_end=5,
            representative_timestamp=4.5,
            padding_before=1.5,
            padding_after=0.5,
        )
        self.assertEqual(result.clip_start_seconds, 2.5)
        self.assertEqual(result.clip_end_seconds, 5.5)
        self.assertEqual(result.applied_padding_before_seconds, 1.5)
        self.assertEqual(result.applied_padding_after_seconds, 0.5)

    def test_point_evidence_always_has_positive_bounded_duration(self):
        result = self.resolve(
            source_start=0,
            source_end=0,
            representative_timestamp=0,
            padding_before=0,
            padding_after=0,
        )
        self.assertGreater(result.clip_end_seconds, result.clip_start_seconds)
        self.assertLessEqual(result.clip_end_seconds, 5)


class EvidenceDeliveryTests(unittest.TestCase):
    def service(self):
        artifacts = Mock()
        artifacts.create_evidence_frame.return_value = (
            artifact(
                ArtifactKind.evidence_frame,
                "623456781234423481234567890abcde",
                "image/png",
            ),
            64,
            48,
        )
        artifacts.create_snippet.return_value = artifact(
            ArtifactKind.snippet,
            "723456781234423481234567890abcde",
            "video/mp4",
        )
        media = Mock()
        media.require_record.return_value = SimpleNamespace(duration_seconds=10.0)
        return EvidenceDeliveryService(
            artifacts=artifacts,
            media=media,
            max_clip_duration_seconds=5.0,
        ), artifacts

    def test_scene_delivery_uses_exact_index_and_renders_clip_in_same_job(self):
        service, artifacts = self.service()
        result = service.deliver_search(
            fused(),
            EvidenceDeliveryPolicy(
                mode=EvidenceDeliveryMode.keyframes_and_clips,
                max_items=1,
            ),
            execution=ExecutionContext(job_id=JOB_ID),
        )

        item = result.evidence_delivery.items[0]
        self.assertEqual(item.evidence_id, result.moments[0].moment_id)
        self.assertEqual(item.keyframe.match, EvidenceFrameMatch.exact_indexed_frame)
        self.assertEqual(item.state, EvidenceDeliveryState.ready)
        self.assertEqual(item.keyframe.frame_index, 7)
        self.assertEqual(item.keyframe.timestamp_seconds, 1.4)
        self.assertEqual(item.keyframe.width, 64)
        self.assertEqual(item.keyframe.artifact.artifact.mime_type, "image/png")
        self.assertIsNone(item.keyframe.artifact.resource_uri)
        self.assertIsNotNone(item.clip)
        self.assertIsNone(item.clip.resource_uri)
        frame_call = artifacts.create_evidence_frame.call_args.kwargs
        self.assertEqual(frame_call["frame_index"], 7)
        self.assertEqual(frame_call["timestamp_seconds"], 1.4)
        clip_command = artifacts.create_snippet.call_args.args[0]
        self.assertGreaterEqual(clip_command.start_seconds, 0)
        self.assertLessEqual(clip_command.end_seconds, 10)

    def test_frame_and_clip_failures_do_not_erase_retrieval(self):
        service, artifacts = self.service()
        artifacts.create_evidence_frame.side_effect = RuntimeError("ffmpeg missing")
        artifacts.create_snippet.side_effect = RuntimeError("render failed")
        result = service.deliver_search(
            fused(),
            EvidenceDeliveryPolicy(
                mode=EvidenceDeliveryMode.keyframes_and_clips,
                max_items=1,
            ),
            execution=ExecutionContext(job_id=JOB_ID),
        )
        self.assertEqual(len(result.moments), 1)
        item = result.evidence_delivery.items[0]
        self.assertIsNone(item.keyframe)
        self.assertIsNone(item.clip)
        self.assertEqual(item.state, EvidenceDeliveryState.failed)
        self.assertEqual(
            [error.code for error in item.errors],
            ["evidence_frame_delivery_failed", "evidence_clip_delivery_failed"],
        )

    def test_on_demand_resolution_rejects_foreign_evidence(self):
        service, _artifacts = self.service()
        with self.assertRaises(ApplicationError) as raised:
            service.resolve_job_evidence(
                fused(),
                "f" * 64,
                padding_before=2,
                padding_after=2,
            )
        self.assertEqual(raised.exception.code, "evidence_not_in_source_job")

    def test_selected_delivery_materializes_only_requested_job_evidence(self):
        service, artifacts = self.service()
        source = fused()
        evidence_id = source.moments[0].moment_id

        delivery = service.deliver_selected(
            source,
            (evidence_id,),
            EvidenceDeliveryPolicy(
                mode=EvidenceDeliveryMode.keyframes_and_clips,
                max_items=1,
            ),
        )

        self.assertEqual([item.evidence_id for item in delivery.items], [evidence_id])
        self.assertIsNotNone(delivery.items[0].keyframe)
        self.assertIsNotNone(delivery.items[0].clip)
        artifacts.create_evidence_frame.assert_called_once()
        artifacts.create_snippet.assert_called_once()

        with self.assertRaises(ApplicationError) as raised:
            service.deliver_selected(
                source,
                ("f" * 64,),
                EvidenceDeliveryPolicy(
                    mode=EvidenceDeliveryMode.keyframes,
                    max_items=1,
                ),
            )
        self.assertEqual(raised.exception.code, "evidence_not_in_source_job")

    def test_range_resolution_failure_is_scoped_to_the_evidence_item(self):
        service, _artifacts = self.service()
        service.media.require_record.side_effect = RuntimeError("media unavailable")
        result = service.deliver_search(
            fused(),
            EvidenceDeliveryPolicy(
                mode=EvidenceDeliveryMode.keyframes_and_clips,
                max_items=1,
            ),
            execution=ExecutionContext(job_id=JOB_ID),
        )
        self.assertEqual(len(result.moments), 1)
        item = result.evidence_delivery.items[0]
        self.assertIsNone(item.range)
        self.assertEqual(item.state, EvidenceDeliveryState.failed)
        self.assertEqual(item.errors[0].code, "evidence_range_resolution_failed")

    def test_query_delivery_preserves_existing_evidence_identity(self):
        service, _artifacts = self.service()
        hit = scene_hit()
        evidence = MomentEvidence(
            evidence_id="d" * 64,
            snapshot_id=SNAPSHOT_ID,
            media_id=MEDIA_ID,
            generation_id=GENERATION_ID,
            modality="scene",
            source_id=hit.source_id,
            start=hit.start,
            end=hit.end,
            hit=hit,
        )
        answer = QueryAnswer(
            question="Which frame is green?",
            mode=QueryAnswerMode.evidence_only,
            plan=QueryPlan(
                steps=(SearchMomentsPlanStep(modality="scene", query="green frame"),)
            ),
            evidence=(evidence,),
            moments=fused().moments,
            fusion=FusionProvenance(
                requested_modalities=("scene",),
                searched_modalities=("scene",),
            ),
            fallback_reason="query_model_not_configured",
        )
        delivered = service.deliver_query(
            answer,
            EvidenceDeliveryPolicy(
                mode=EvidenceDeliveryMode.keyframes,
                max_items=1,
            ),
            execution=ExecutionContext(job_id=JOB_ID),
        )
        self.assertEqual(delivered.evidence[0].evidence_id, "d" * 64)
        self.assertEqual(
            delivered.evidence_delivery.items[0].evidence_id,
            delivered.evidence[0].evidence_id,
        )


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required")
class ExactFrameRendererTests(unittest.TestCase):
    def test_extracts_the_requested_frame_index(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is unavailable in this test profile")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "colors.mp4"
            destination = root / "frame.png"
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=red:s=32x32:d=1:r=1",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=green:s=32x32:d=1:r=1",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=blue:s=32x32:d=1:r=1",
                    "-filter_complex",
                    "[0:v][1:v][2:v]concat=n=3:v=1:a=0",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-y",
                    str(source),
                ],
                check=True,
            )
            FFmpegFrameRenderer().render(
                source,
                destination,
                timestamp_seconds=1.0,
                frame_index=1,
                cancellation=CancellationToken(),
                progress=None,
            )
            with Image.open(destination) as image:
                red, green, blue = image.convert("RGB").getpixel((16, 16))
                dimensions = image.size
            self.assertEqual(dimensions, (32, 32))
            self.assertGreater(green, red)
            self.assertGreater(green, blue)

    def test_real_artifact_pipeline_persists_frame_and_clip_idempotently(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "colors.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=red:s=64x48:d=1:r=1",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=green:s=64x48:d=1:r=1",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=blue:s=64x48:d=1:r=1",
                    "-filter_complex",
                    "[0:v][1:v][2:v]concat=n=3:v=1:a=0",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-y",
                    str(source),
                ],
                check=True,
            )
            settings = VidXPSettings(
                repository_root=root / "repository",
                data_dir=root / "data",
                runtime_backend="cpu",
            )
            settings.layout.ensure_local_directories()
            catalog = LocalCatalog(settings.layout.catalog)
            probe = FFprobeMediaProbe()
            media = MediaService(
                settings=settings,
                catalog=catalog,
                store=LocalMediaStore(
                    settings.layout.media,
                    max_bytes=settings.max_local_import_bytes,
                ),
                probe=probe,
            )
            imported = media.import_local(ImportMediaCommand(path=source))
            artifacts = ArtifactService(
                catalog=catalog,
                store=LocalArtifactStore(settings.layout.artifacts),
                media=media,
                probe=probe,
                actor_renderer=Mock(),
                snippet_renderer=FFmpegSnippetRenderer(),
                frame_renderer=FFmpegFrameRenderer(),
                max_snippet_duration_seconds=5,
            )
            delivery_service = EvidenceDeliveryService(
                artifacts=artifacts,
                media=media,
                max_clip_duration_seconds=5,
            )
            hit = scene_hit().model_copy(
                update={
                    "media_id": imported.media_id,
                    "video_id": imported.media_id,
                    "start": 1.0,
                    "end": 2.0,
                    "metadata": {
                        "frame_index": 1,
                        "timestamp": 1.0,
                        "fps": 1.0,
                    },
                }
            )
            search = fuse_search_results(
                query="green frame",
                requested_modalities=("scene",),
                results=(
                    SearchResult(
                        query_id="scene:green",
                        query="green frame",
                        modality="scene",
                        hits=(hit,),
                    ),
                ),
                snapshot_id=SNAPSHOT_ID,
            )
            policy = EvidenceDeliveryPolicy(
                mode=EvidenceDeliveryMode.keyframes_and_clips,
                max_items=1,
                padding_before_seconds=0.25,
                padding_after_seconds=0.25,
            )
            first = delivery_service.deliver_search(
                search, policy, execution=ExecutionContext(job_id=JOB_ID)
            )
            repeated = delivery_service.deliver_search(
                search, policy, execution=ExecutionContext(job_id=JOB_ID)
            )
            first_item = first.evidence_delivery.items[0]
            repeated_item = repeated.evidence_delivery.items[0]
            self.assertEqual(first_item.errors, ())
            frame = first_item.keyframe.artifact.artifact
            clip = first_item.clip.artifact
            frame_content = artifacts.content(frame.artifact_id)
            clip_content = artifacts.content(clip.artifact_id)

            self.assertEqual(frame.mime_type, "image/png")
            self.assertEqual(frame_content.path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(
                frame.sha256,
                hashlib.sha256(frame_content.path.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                probe.probe(clip_content.path).detected_mime_type, "video/mp4"
            )
            self.assertEqual(
                repeated_item.keyframe.artifact.artifact.artifact_id,
                frame.artifact_id,
            )
            self.assertEqual(repeated_item.clip.artifact.artifact_id, clip.artifact_id)


if __name__ == "__main__":
    unittest.main()
