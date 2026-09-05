import unittest
from unittest.mock import Mock, patch

from pydantic import ValidationError

from vidxp.capabilities.action.config import VideoPrismConfig
from vidxp.capabilities.action.indexing import (
    CLIP_FRAMES,
    VISUAL_PROCESSOR,
    VideoPrismIndexState,
    process_videoprism_samples,
)
from vidxp.capabilities.action.models import normalize_pooled_output
from vidxp.capabilities.action.specs import VIDEOPRISM_MODEL
from vidxp.core.contracts import CancellationToken, IndexConfig, VideoSource
from vidxp.core.video import FrameSample, VideoInfo


class VideoPrismTests(unittest.TestCase):
    def test_provider_pooler_dimension_is_removed(self):
        import torch

        embedding = normalize_pooled_output(torch.tensor([[[3.0, 4.0]]]))

        self.assertEqual(tuple(embedding.shape), (1, 2))
        self.assertAlmostEqual(embedding[0, 0].item(), 0.6)
        self.assertAlmostEqual(embedding[0, 1].item(), 0.8)

    def test_config_rejects_invalid_sampling(self):
        with self.assertRaises(ValidationError):
            VideoPrismConfig(sample_fps=0)

    def test_streaming_index_groups_clips_and_pads_only_the_tail(self):
        config = IndexConfig(
            video_id="video-1",
            enabled_modalities=("action",),
        )
        info = VideoInfo(30.0, 270, 9.0, 2, 2)
        samples = [
            FrameSample(index * 15, index / 2, object())
            for index in range(18)
        ]
        state = VideoPrismIndexState(provider=Mock())
        storage = Mock()
        storage.upsert.side_effect = lambda _name, records, **_kwargs: len(
            records
        )

        with patch(
            "vidxp.capabilities.action.indexing.encode_video_clips",
            side_effect=lambda clips, _provider: [[0.1] for _ in clips],
        ) as encode:
            process_videoprism_samples(
                samples,
                state=state,
                info=info,
                config=config,
                storage=storage,
                cancellation=CancellationToken(),
            )
            summary, operations = VISUAL_PROCESSOR.finalize(
                state,
                config=config,
                storage=storage,
            )

        self.assertEqual(summary, {"videoprism_clips": 2})
        self.assertEqual(operations, 2)
        self.assertEqual(
            [call.args[0][0].sample_count for call in encode.call_args_list],
            [CLIP_FRAMES, CLIP_FRAMES],
        )
        tail = storage.upsert.call_args_list[1].args[1][0]
        self.assertEqual(tail.metadata["sample_count"], 2)
        self.assertEqual(
            (tail.metadata["start"], tail.metadata["end"]),
            (8.0, 9.0),
        )

    def test_model_contract_pins_the_pytorch_checkpoint(self):
        self.assertEqual(
            VIDEOPRISM_MODEL.model_id,
            "google/videoprism-lvt-base-f16r288",
        )
        self.assertEqual(VIDEOPRISM_MODEL.weights_file, "model.safetensors")
        self.assertEqual(VIDEOPRISM_MODEL.license, "Apache-2.0")
        self.assertEqual(VIDEOPRISM_MODEL.download_size_bytes, 993_993_146)


    def test_scene_clip_mode_flushes_clips_at_shot_boundaries(self):
        config = IndexConfig(
            video_id="video-1",
            enabled_modalities=("action",),
            capability_options={"action": {"clip_mode": "scene"}},
        )
        source = VideoSource(path="fake.mp4")

        with (
            patch(
                "vidxp.capabilities.action.indexing.detect_shot_boundaries",
                return_value=[2.0, 5.0],
            ) as boundaries_mock,
            patch(
                "vidxp.capabilities.action.indexing.get_videoprism_model",
                return_value=Mock(),
            ),
        ):
            state = VISUAL_PROCESSOR.prepare(config, Mock(), None, source=source)

        boundaries_mock.assert_called_once_with("fake.mp4")
        self.assertEqual(state.accumulator.boundaries, [2.0, 5.0])

        info = VideoInfo(30.0, 270, 9.0, 2, 2)
        samples = [
            FrameSample(index * 15, index / 2, object())
            for index in range(18)
        ]
        storage = Mock()
        storage.upsert.side_effect = lambda _name, records, **_kwargs: len(
            records
        )

        with patch(
            "vidxp.capabilities.action.indexing.encode_video_clips",
            side_effect=lambda clips, _provider: [[0.1] for _ in clips],
        ):
            process_videoprism_samples(
                samples,
                state=state,
                info=info,
                config=config,
                storage=storage,
                cancellation=CancellationToken(),
            )
            summary, operations = VISUAL_PROCESSOR.finalize(
                state,
                config=config,
                storage=storage,
            )

        self.assertEqual(summary, {"videoprism_clips": 3})
        self.assertEqual(operations, 3)

        records = [call.args[1][0] for call in storage.upsert.call_args_list]
        self.assertEqual(
            [record.metadata["sample_count"] for record in records],
            [4, 6, 8],
        )
        self.assertEqual(
            [
                (record.metadata["start"], record.metadata["end"])
                for record in records
            ],
            [(0.0, 2.0), (2.0, 5.0), (5.0, 9.0)],
        )


if __name__ == "__main__":
    unittest.main()
