from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, call, patch
import wave

from vidxp.capabilities.sound.config import SoundConfig
from vidxp.capabilities.sound.indexing import (
    AudioWindow,
    index_sound,
    iter_audio_windows,
    sound_records,
)
from vidxp.capabilities.sound.models import _offline_roberta_tokenizer
from vidxp.capabilities.sound.operations import search_sound
from vidxp.capabilities.sound.specs import (
    FINELAP_MODEL,
    ROBERTA_CONFIG,
    ROBERTA_MERGES,
    ROBERTA_VOCAB,
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
            capability_options={"sound": options},
        )

    def test_config_limits_windows_to_finelap_input_length(self):
        self.assertEqual(SoundConfig().window_seconds, 10.0)
        with self.assertRaises(ValueError):
            SoundConfig(window_seconds=10.1)

    def test_specs_pin_model_and_explicit_tokenizer_assets(self):
        self.assertEqual(FINELAP_MODEL.model_id, "AndreasXi/FineLAP")
        self.assertEqual(len(FINELAP_MODEL.revision), 40)
        self.assertEqual(ROBERTA_VOCAB.revision, ROBERTA_MERGES.revision)
        self.assertEqual(ROBERTA_CONFIG.revision, ROBERTA_VOCAB.revision)
        self.assertIn(ROBERTA_CONFIG.revision, ROBERTA_CONFIG.url)
        self.assertIn(ROBERTA_VOCAB.revision, ROBERTA_VOCAB.url)
        self.assertIn(ROBERTA_MERGES.revision, ROBERTA_MERGES.url)

    @patch("transformers.RobertaTokenizer")
    def test_offline_tokenizer_uses_transformers_5_asset_arguments(
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

    def test_records_include_window_and_dense_activation_intervals(self):
        config = self.config()
        windows = (
            AudioWindow(0, 0.0, 10.0, b""),
            AudioWindow(1, 10.0, 12.0, b""),
        )
        global_embeddings = (Vector([1.0, 0.0]), Vector([0.5, 0.5]))
        dense_embeddings = (
            [Vector([1.0, 0.0]) for _ in range(64)],
            [Vector([0.5, 0.5]) for _ in range(64)],
        )

        records = sound_records(
            windows,
            global_embeddings,
            dense_embeddings,
            config,
        )

        self.assertEqual(len(records), 78)
        self.assertEqual(records[0].metadata["representation"], "window")
        self.assertEqual(records[1].metadata["representation"], "activation")
        self.assertEqual(records[1].metadata["start"], 0.0)
        self.assertEqual(records[1].metadata["end"], 0.16)
        self.assertAlmostEqual(records[-1].metadata["start"], 11.92)
        self.assertEqual(records[-1].metadata["end"], 12.0)
        self.assertTrue(
            all(record.metadata["generation_id"] == GENERATION_ID for record in records)
        )

    def test_audio_decode_resamples_and_preserves_source_duration(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "sample.wav"
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(8_000)
                output.writeframes(b"\0\0" * 4_000)

            windows = list(
                iter_audio_windows(
                    path,
                    window_seconds=10.0,
                    cancellation=CancellationToken(),
                )
            )

        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].start, 0.0)
        self.assertEqual(windows[0].end, 0.5)
        self.assertEqual(len(windows[0].pcm), 16_000)

    def test_index_labels_global_and_dense_records_for_filtered_search(self):
        config = self.config()
        windows = (
            AudioWindow(0, 0.0, 10.0, b"\0\0" * 16),
            AudioWindow(1, 10.0, 12.0, b"\0\0" * 16),
        )
        provider = Mock()
        provider.encode_audio.return_value = (
            (Vector([1.0]), Vector([2.0])),
            (
                [Vector([1.0]) for _ in range(64)],
                [Vector([2.0]) for _ in range(64)],
            ),
        )
        storage = Mock()
        storage.upsert.return_value = 78

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

        self.assertEqual(
            summary,
            {"sound_windows": 2, "sound_activations": 76},
        )
        self.assertEqual(storage.upsert.call_count, 2)
        self.assertTrue(
            all(call.args[0] == "sound" for call in storage.upsert.call_args_list)
        )
        self.assertEqual(
            sum(len(call.args[1]) for call in storage.upsert.call_args_list),
            78,
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

        self.assertEqual(
            summary,
            {"sound_windows": 0, "sound_activations": 0},
        )
        get_model.assert_not_called()

    def test_sound_search_uses_global_windows_to_scope_dense_ranking(self):
        config = self.config()
        storage = Mock()
        storage.query.side_effect = [
            [
                {
                    "source_id": "sound:window:3",
                    "raw_distance": 0.2,
                    "metadata": {
                        **config.record_identity("sound", "sound:window:3"),
                        "generation_id": GENERATION_ID,
                        "representation": "window",
                        "window_index": 3,
                        "start": 30.0,
                        "end": 40.0,
                    },
                },
            ],
            [
                {
                    "source_id": "sound:activation:3:9",
                    "raw_distance": 0.1,
                    "metadata": {
                        **config.record_identity("sound", "sound:activation:3:9"),
                        "generation_id": GENERATION_ID,
                        "representation": "activation",
                        "window_index": 3,
                        "activation_index": 9,
                        "start": 31.44,
                        "end": 31.6,
                        "private": "hidden",
                    },
                },
            ],
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
            )

        self.assertEqual(result.modality, "sound")
        self.assertEqual(result.hits[0].start, 31.44)
        self.assertEqual(
            result.hits[0].metadata,
            {
                "representation": "activation",
                "window_index": 3,
                "activation_index": 9,
                "context_source_id": "sound:window:3",
                "context_start": 30.0,
                "context_end": 40.0,
                "context_rank": 1,
            },
        )
        self.assertEqual(provider.encode_text.call_count, 1)
        self.assertEqual(
            storage.query.call_args_list,
            [
                call(
                    "sound",
                    [0.1, 0.2],
                    top_k=10,
                    video_id=None,
                    filters={"representation": "window"},
                ),
                call(
                    "sound",
                    [0.1, 0.2],
                    top_k=10,
                    video_id=None,
                    filters={
                        "representation": "activation",
                        "video_id": MEDIA_ID,
                        "window_index": 3,
                    },
                ),
            ],
        )


if __name__ == "__main__":
    unittest.main()
