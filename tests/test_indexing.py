import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from vidxp.capabilities.actor.indexing import (
    _actor_cluster_id,
    _actor_cluster_records,
    _actor_records,
)
from vidxp.capabilities.contracts import (
    CapabilityDefinition,
    CapabilityExecutor,
    CapabilityPlugin,
)
from vidxp.capabilities.dialogue.indexing import (
    build_dialogue_phrases,
    index_dialogue,
)
from vidxp.capabilities.scene.indexing import encode_scene_batch
from vidxp.capabilities.scene.models import SceneModel
from vidxp.capabilities.registry import (
    CapabilityRegistry,
    create_capability_registry,
)
from vidxp.capabilities.visual import index_visuals
from vidxp.core.contracts import (
    CancellationToken,
    IndexConfig,
    VideoSource,
)
from vidxp.core.video import FrameSample, VideoInfo
from vidxp.runtime import ModelRuntime
from vidxp.settings import VidXPSettings


class CapturingStorage:
    def __init__(self):
        self.calls = []

    def upsert(self, modality, records, **options):
        records = list(records)
        self.calls.append((modality, records, options))
        return len(records)


class FakeEncoder:
    def __init__(self):
        self.batches = []

    def encode_document(self, texts, **_):
        self.batches.append(list(texts))
        return np.asarray(
            [[float(index), 1.0] for index, _ in enumerate(texts)]
        )


class IndexingTests(unittest.TestCase):
    def runtime(self) -> ModelRuntime:
        return ModelRuntime(
            VidXPSettings(
                repository_root="unused",
                runtime_backend="cpu",
            )
        )

    def test_word_timestamps_are_grouped_without_interpolation(self):
        phrases = build_dialogue_phrases(
            [
                {
                    "text": "ignored aggregate text",
                    "start": 0.0,
                    "end": 4.0,
                    "words": [
                        {"word": "one", "start": 0.1, "end": 0.5},
                        {"word": "two", "start": 0.6, "end": 1.0},
                        {"word": "three", "start": 1.2, "end": 1.8},
                    ],
                }
            ],
            words_per_phrase=2,
        )

        self.assertEqual(
            [(item.text, item.start, item.end) for item in phrases],
            [("one two", 0.1, 1.0), ("three", 1.2, 1.8)],
        )

    def test_siglip2_uses_transformers_five_pooled_image_output(self):
        import torch

        processor = Mock(
            return_value={"pixel_values": torch.ones((1, 3, 2, 2))}
        )
        model = Mock()
        model.get_image_features.return_value = SimpleNamespace(
            pooler_output=torch.tensor([[3.0, 4.0]])
        )
        provider = SceneModel(model=model, processor=processor, device="cpu")
        sample = FrameSample(
            frame_index=0,
            timestamp=0.0,
            frame=np.zeros((2, 2, 3), dtype=np.uint8),
        )

        vectors = encode_scene_batch([sample], provider)

        self.assertEqual(vectors, [[0.6000000238418579, 0.800000011920929]])

    def test_segment_text_uses_interpolated_timestamps_as_fallback(self):
        phrases = build_dialogue_phrases(
            [
                {
                    "text": "one two three four five six seven",
                    "start": 0.0,
                    "end": 7.0,
                }
            ],
            words_per_phrase=5,
        )

        self.assertEqual(
            [(item.text, item.start, item.end) for item in phrases],
            [
                ("one two three four five", 0.0, 5.0),
                ("six seven", 5.0, 7.0),
            ],
        )

    def test_transcript_indexing_batches_without_transcription(self):
        config = IndexConfig(
            dataset="hirest",
            split="test",
            run_id="asr",
            video_id="video-1",
            enabled_modalities=("dialogue",),
            capability_options={
                "dialogue": {"embedding_batch_size": 2},
            },
        )
        source = VideoSource(
            video_id="video-1",
            transcript=(
                {"text": "first", "start": 0.0, "end": 1.0},
                {"text": "second", "start": 1.0, "end": 2.0},
                {"text": "third", "start": 2.0, "end": 3.0},
            ),
        )
        storage = CapturingStorage()
        encoder = FakeEncoder()
        with (
            patch(
                "vidxp.capabilities.dialogue.indexing.get_embedder",
                return_value=encoder,
            ),
            patch(
                "vidxp.capabilities.dialogue.indexing.transcribe_video",
                side_effect=AssertionError("transcription was used"),
            ),
        ):
            stats = index_dialogue(
                source,
                config=config,
                storage=storage,
                cancellation=CancellationToken(),
                runtime=self.runtime(),
            )

        self.assertEqual([len(batch) for batch in encoder.batches], [2, 1])
        self.assertEqual(stats["dialogue_phrases"], 3)

    def test_visual_stream_uses_registered_processor_without_name_switches(self):
        processor = Mock()
        processor.batch_size.return_value = 1
        processor.prepare.return_value = object()
        processor.finalize.return_value = ({"ocr_frames": 1}, 1)
        definition = CapabilityDefinition(
            name="ocr",
            description="OCR.",
            extra="ocr",
            collection_name="ocr",
            index_stage="visual_indexing",
            execution_group="visual",
        )
        registry = CapabilityRegistry(
            (
                CapabilityPlugin(
                    definition=definition,
                    executor_factory=lambda: CapabilityExecutor(
                        indexer=index_visuals,
                        index_processor=processor,
                    ),
                ),
            )
        )
        config = IndexConfig(
            video_id="video-1",
            enabled_modalities=("ocr",),
            collection_names={"ocr": "ocr"},
        )
        info = VideoInfo(10.0, 1, 0.1, 2, 2)
        frame = np.zeros((2, 2, 3), dtype=np.uint8)

        def stream(*_, **options):
            options["stats"].frames_advanced = 1
            options["stats"].frames_materialized = 1
            return iter([[FrameSample(0, 0.0, frame)]])

        with (
            patch(
                "vidxp.capabilities.visual.probe_video",
                return_value=info,
            ),
            patch(
                "vidxp.capabilities.visual.iter_frame_batches",
                side_effect=stream,
            ),
        ):
            result = index_visuals(
                VideoSource(video_id="video-1", path="unused.mp4"),
                config=config,
                storage=CapturingStorage(),
                cancellation=CancellationToken(),
                registry=registry,
                runtime=self.runtime(),
            )

        self.assertEqual(result.summary["ocr_frames"], 1)
        processor.prepare.assert_called_once()
        processor.process.assert_called_once()

    def test_scene_and_actor_share_one_frame_stream(self):
        registry = create_capability_registry()
        scene = registry.executor("scene").index_processor
        actor = registry.executor("actor").index_processor
        scene.prepare = Mock(return_value=object())
        actor.prepare = Mock(return_value=object())
        scene.process = Mock()
        actor.process = Mock()
        scene.batch_size = Mock(return_value=2)
        actor.batch_size = Mock(return_value=1)
        scene.finalize = Mock(return_value=({"scene_frames": 2}, 2))
        actor.finalize = Mock(
            return_value=(
                {"actor_frames": 2, "actor_detections": 0, "actor_clusters": 0},
                2,
            )
        )
        info = VideoInfo(10.0, 2, 0.2, 2, 2)
        frame = np.zeros((2, 2, 3), dtype=np.uint8)
        def stream(*_, **options):
            options["stats"].frames_advanced = 2
            options["stats"].frames_materialized = 2
            return iter(
                [[FrameSample(0, 0.0, frame), FrameSample(1, 0.1, frame)]]
            )

        frame_stream = Mock(side_effect=stream)
        config = IndexConfig(
            video_id="video-1",
            enabled_modalities=("scene", "actor"),
        )
        with (
            patch(
                "vidxp.capabilities.visual.probe_video",
                return_value=info,
            ),
            patch(
                "vidxp.capabilities.visual.iter_frame_batches",
                frame_stream,
            ),
        ):
            result = index_visuals(
                VideoSource(video_id="video-1", path="unused.mp4"),
                config=config,
                storage=CapturingStorage(),
                cancellation=CancellationToken(),
                registry=registry,
                runtime=self.runtime(),
            )

        frame_stream.assert_called_once()
        self.assertEqual(result.summary["processed_frames"], 2)
        self.assertEqual(result.summary["scene_frames"], 2)
        self.assertEqual(result.summary["actor_frames"], 2)

    def test_actor_records_preserve_stable_detection_metadata(self):
        config = IndexConfig(
            dataset="sample",
            split="test",
            run_id="actors",
            video_id="video-1",
            generation_id="generation-1",
            enabled_modalities=("actor",),
        )
        cluster_id = _actor_cluster_id(config, 1)
        records = _actor_records(
            [
                {
                    "detection_id": "d000000000000-0000",
                    "cluster_id": cluster_id,
                    "frame_index": 0,
                    "timestamp": 0.0,
                    "bbox": (1, 2, 3, 0),
                }
            ],
            config,
        )

        self.assertEqual(records[0].metadata["cluster_id"], cluster_id)
        self.assertEqual(
            cluster_id,
            "generation-1:actors:video-1:actor-cluster:1",
        )
        self.assertEqual(records[0].metadata["bbox_top"], 1)

    def test_actor_cluster_identity_is_unique_by_media_and_generation(self):
        def config(video_id, generation_id):
            return IndexConfig(
                run_id="actors",
                video_id=video_id,
                generation_id=generation_id,
                enabled_modalities=("actor",),
            )

        identities = {
            _actor_cluster_id(config("video-1", "generation-1"), 1),
            _actor_cluster_id(config("video-2", "generation-1"), 1),
            _actor_cluster_id(config("video-1", "generation-2"), 1),
        }

        self.assertEqual(len(identities), 3)

    def test_actor_cluster_summaries_are_materialized_for_bounded_paging(self):
        config = IndexConfig(
            run_id="actors",
            video_id="video-1",
            generation_id="generation-1",
            enabled_modalities=("actor",),
        )

        records = _actor_cluster_records(
            {"cluster-1": 3},
            {"cluster-1": (1.25, 4.5)},
            config,
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].metadata["record_kind"], "cluster_summary")
        self.assertEqual(
            records[0].metadata["summary_cluster_id"],
            "cluster-1",
        )
        self.assertEqual(records[0].metadata["detection_count"], 3)
        self.assertEqual(records[0].metadata["first_timestamp"], 1.25)
        self.assertEqual(records[0].metadata["last_timestamp"], 4.5)


if __name__ == "__main__":
    unittest.main()
