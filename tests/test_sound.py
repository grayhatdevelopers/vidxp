from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, call, patch
import wave

from vidxp.capabilities.sound.config import SoundConfig, sound_config
from vidxp.capabilities.sound.indexing import (
    AudioWindow,
    index_sound,
    iter_audio_windows,
    sound_records,
)
from vidxp.capabilities.sound.models import _offline_roberta_tokenizer
from vidxp.capabilities.sound.operations import search_sound
from vidxp.capabilities.sound.specs import (
    PE_A_FRAME_INTERVAL_SECONDS,
    PE_A_FRAME_MODEL,
    PE_A_SAMPLE_RATE,
)
from vidxp.core.contracts import CancellationToken, IndexConfig, VideoSource


MEDIA_ID = "123456781234423481234567890abcde"
GENERATION_ID = "223456781234423481234567890abcde"


class Vector(list):
    def tolist(self):
        return list(self)


class SoundTests(unittest.TestCase):
    def config(self, **options):
        return IndexConfig.local(
            video_id=MEDIA_ID,
            enabled_modalities=("sound",),
            generation_id=GENERATION_ID,
            capability_options={"sound": SoundConfig(**options).model_dump()},
        )

    def test_config_declares_bounded_section_and_evidence_defaults(self):
        settings = SoundConfig()

        self.assertEqual(settings.batch_size, 1)
        self.assertEqual(settings.inference_window_seconds, 10.0)
        self.assertEqual(settings.inference_overlap_seconds, 2.0)
        self.assertEqual(settings.evidence_window_seconds, 10.0)
        with self.assertRaisesRegex(ValueError, "must be smaller"):
            SoundConfig(
                inference_window_seconds=10,
                inference_overlap_seconds=10,
            )
        with self.assertRaisesRegex(ValueError, "inner-product"):
            sound_config(
                IndexConfig.local(
                    enabled_modalities=("sound",),
                    capability_options={"sound": settings.model_dump()},
                    vector_distance="l2",
                )
            )

    def test_spec_pins_selected_frame_model_contract(self):
        self.assertEqual(PE_A_FRAME_MODEL.model_id, "facebook/pe-a-frame-small")
        self.assertEqual(len(PE_A_FRAME_MODEL.revision), 40)
        self.assertEqual(PE_A_SAMPLE_RATE, 48_000)
        self.assertEqual(PE_A_FRAME_INTERVAL_SECONDS, 0.04)
        self.assertEqual(PE_A_FRAME_MODEL.license, "Apache-2.0")

    @patch("transformers.RobertaTokenizer")
    def test_finelap_control_tokenizer_remains_offline(
        self,
        tokenizer_class,
    ):
        tokenizer = tokenizer_class.return_value
        tokenizer.return_value = {"input_ids": [42]}

        result = _offline_roberta_tokenizer(
            vocab_path="vocab.json",
            merges_path="merges.txt",
        )

        self.assertIs(result, tokenizer)
        tokenizer_class.assert_called_once_with(
            vocab="vocab.json",
            merges="merges.txt",
            model_max_length=512,
        )

    def test_records_map_frames_once_and_return_fixed_evidence_windows(self):
        windows = (
            AudioWindow(0, 0.0, 0.12, b"", 0.0, 0.08),
            AudioWindow(1, 0.04, 0.16, b"", 0.08, 0.16),
        )
        embeddings = (
            [Vector([1.0, 0.0]) for _ in range(3)],
            [Vector([0.5, 0.5]) for _ in range(3)],
        )

        records = sound_records(
            windows,
            embeddings,
            self.config(evidence_window_seconds=0.1),
            evidence_window_seconds=0.1,
        )

        self.assertEqual(len(records), 4)
        self.assertEqual(
            [record.metadata["timestamp"] for record in records],
            [0.0, 0.04, 0.08, 0.12],
        )
        self.assertEqual(records[0].metadata["representation"], "frame")
        self.assertEqual(records[0].metadata["start"], 0.0)
        self.assertEqual(records[0].metadata["end"], 0.1)
        self.assertEqual(records[-1].metadata["start"], 0.1)
        self.assertEqual(records[-1].metadata["end"], 0.2)
        self.assertTrue(
            all(
                record.metadata["generation_id"] == GENERATION_ID
                for record in records
            )
        )

    def test_audio_decode_resamples_and_assigns_overlap_once(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "sample.wav"
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(8_000)
                output.writeframes(b"\0\0" * 20_000)

            windows = list(
                iter_audio_windows(
                    path,
                    window_seconds=2.0,
                    overlap_seconds=1.0,
                    sample_rate=8_000,
                    cancellation=CancellationToken(),
                )
            )

        self.assertEqual(len(windows), 2)
        self.assertEqual(
            [(item.start, item.end) for item in windows],
            [(0.0, 2.0), (1.0, 2.5)],
        )
        self.assertEqual(windows[0].owned_end, 1.5)
        self.assertEqual(windows[1].owned_start, 1.5)
        self.assertEqual(len(windows[0].pcm), 32_000)
        self.assertEqual(len(windows[1].pcm), 24_000)

    def test_index_stores_pe_a_frames_through_shared_storage(self):
        config = self.config()
        windows = (
            AudioWindow(0, 0.0, 0.08, b"\0\0" * 16),
            AudioWindow(1, 0.08, 0.12, b"\0\0" * 16),
        )
        provider = Mock()
        provider.encode_audio.side_effect = [
            ([Vector([1.0]), Vector([2.0])],),
            ([Vector([3.0])],),
        ]
        storage = Mock()
        storage.upsert.side_effect = [2, 1]

        with (
            TemporaryDirectory() as directory,
            patch(
                "vidxp.capabilities.sound.indexing.iter_audio_windows",
                return_value=iter(windows),
            ),
            patch(
                "vidxp.capabilities.sound.indexing.get_sound_model",
                return_value=provider,
            ),
        ):
            summary = index_sound(
                VideoSource(path=Path(directory) / "video.mp4"),
                config=config,
                storage=storage,
                cancellation=CancellationToken(),
                runtime=Mock(),
            )

        self.assertEqual(summary, {"sound_sections": 2, "sound_frames": 3})
        self.assertEqual(storage.upsert.call_count, 2)
        self.assertTrue(
            all(item.args[0] == "sound" for item in storage.upsert.call_args_list)
        )

    def test_index_skips_media_without_audio_before_loading_model(self):
        with (
            patch(
                "vidxp.capabilities.sound.indexing.iter_audio_windows",
                return_value=iter(()),
            ),
            patch(
                "vidxp.capabilities.sound.indexing.get_sound_model",
            ) as get_model,
        ):
            summary = index_sound(
                VideoSource(path="silent.mp4"),
                config=self.config(),
                storage=Mock(),
                cancellation=CancellationToken(),
                runtime=Mock(),
            )

        self.assertEqual(summary, {"sound_sections": 0, "sound_frames": 0})
        get_model.assert_not_called()

    def test_sound_search_returns_best_frame_from_each_evidence_window(self):
        config = self.config()
        storage = Mock()

        def row(source_id, distance, timestamp, start, evidence_index):
            return {
                "source_id": source_id,
                "raw_distance": distance,
                "metadata": {
                    **config.record_identity("sound", source_id),
                    "generation_id": GENERATION_ID,
                    "representation": "frame",
                    "section_index": 0,
                    "frame_index": round(timestamp / 0.04),
                    "evidence_index": evidence_index,
                    "timestamp": timestamp,
                    "frame_end": timestamp + 0.04,
                    "start": start,
                    "end": start + 10.0,
                },
            }

        storage.query.return_value = [
            row("sound:frame:1", 0.1, 1.0, 0.0, 0),
            row("sound:frame:2", 0.2, 1.04, 0.0, 0),
            row("sound:frame:3", 0.3, 12.0, 10.0, 1),
        ]
        provider = Mock()
        provider.encode_text.return_value = [0.1, 0.2]

        with patch(
            "vidxp.capabilities.sound.operations.get_sound_model",
            return_value=provider,
        ):
            result = search_sound(
                "dog barking",
                config=config,
                runtime=Mock(),
                storage=storage,
                top_k=2,
            )

        self.assertEqual(
            [(hit.start, hit.end, hit.metadata["timestamp"]) for hit in result.hits],
            [(0.0, 10.0, 1.0), (10.0, 20.0, 12.0)],
        )
        self.assertEqual(provider.encode_text.call_count, 1)
        self.assertEqual(
            storage.query.call_args,
            call(
                "sound",
                [0.1, 0.2],
                top_k=500,
                video_id=None,
                filters={"representation": "frame"},
            ),
        )


if __name__ == "__main__":
    unittest.main()
