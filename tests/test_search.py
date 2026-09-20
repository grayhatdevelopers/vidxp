import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from unittest.mock import Mock

import numpy as np
from vidxp.capabilities.speech.operations import search_speech
from vidxp.capabilities.speech.operations import speech_embedding
from vidxp.capabilities.schemas import SearchResult
from vidxp.capabilities.search import (
    distance_to_score,
    serialize_predictions,
    stable_query_id,
)
from vidxp.core.contracts import IndexConfig, IndexSchemaError
from vidxp.runtime import ModelRuntime
from vidxp.settings import VidXPSettings


MEDIA_ID = "123456781234423481234567890abcde"
GENERATION_ID = "223456781234423481234567890abcde"


class FakeStorage:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def query(self, modality, embedding, **options):
        self.calls.append((modality, embedding, options))
        return list(self.rows)


def dialogue_row(source_id, distance, video_id=MEDIA_ID):
    return {
        "source_id": source_id,
        "raw_distance": distance,
        "metadata": {
            "dataset": "sample",
            "split": "test",
            "run_id": "run-1",
            "video_id": video_id,
            "generation_id": GENERATION_ID,
            "source_id": source_id,
            "start": 1.0,
            "end": 2.0,
            "text": "fresh bread",
            "phrase_id": 3,
            "modality": "speech",
        },
    }


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.config = IndexConfig(
            dataset="sample",
            split="test",
            run_id="run-1",
            enabled_modalities=("speech",),
        )
        self.runtime = ModelRuntime(
            VidXPSettings(
                repository_root="unused",
                runtime_backend="cpu",
            )
        )

    def test_top_k_filter_order_distance_and_score_are_preserved(self):
        storage = FakeStorage(
            [
                dialogue_row("run:video-1:dialogue:z", 0.4),
                dialogue_row("run:video-1:dialogue:b", 0.1),
                dialogue_row("run:video-1:dialogue:a", 0.1),
            ]
        )
        with patch(
            "vidxp.capabilities.speech.operations.speech_embedding",
            return_value=[0.5, 0.25],
        ):
            result = search_speech(
                "fresh bread",
                config=self.config,
                runtime=self.runtime,
                top_k=3,
                video_id=MEDIA_ID,
                query_id="query-7",
                storage=storage,
            )

        self.assertEqual(
            [hit.source_id for hit in result.hits],
            [
                "run:video-1:dialogue:a",
                "run:video-1:dialogue:b",
                "run:video-1:dialogue:z",
            ],
        )
        self.assertEqual([hit.rank for hit in result.hits], [1, 2, 3])
        self.assertEqual(result.hits[0].raw_distance, 0.1)
        self.assertEqual(result.hits[0].score, -0.1)
        self.assertEqual(
            result.hits[0].metadata,
            {"text": "fresh bread", "phrase_id": 3},
        )
        self.assertEqual(storage.calls[0][2]["top_k"], 3)
        self.assertEqual(storage.calls[0][2]["video_id"], MEDIA_ID)

    def test_score_is_strictly_monotonic_and_not_a_probability(self):
        self.assertGreater(distance_to_score(0.1), distance_to_score(0.2))
        self.assertEqual(distance_to_score(2.5), -2.5)

    def test_dialogue_query_uses_model_owned_query_prompt(self):
        encoder = Mock()
        encoder.encode_query.return_value = np.asarray([[0.5, 0.25]])
        with patch(
            "vidxp.capabilities.speech.operations.get_embedder",
            return_value=encoder,
        ):
            vector = speech_embedding("fresh bread", self.config, self.runtime)

        self.assertEqual(vector, [0.5, 0.25])
        encoder.encode_query.assert_called_once_with(
            ["fresh bread"],
            convert_to_numpy=True,
            normalize_embeddings=True,
        )

    def test_generated_query_ids_are_scoped_to_the_benchmark_run(self):
        first = stable_query_id("fresh bread", "speech", self.config)
        second = stable_query_id(
            "fresh bread",
            "speech",
            IndexConfig(
                dataset="sample",
                split="test",
                run_id="run-2",
                enabled_modalities=("speech",),
            ),
        )

        self.assertNotEqual(first, second)

    def test_nonpositive_top_k_is_rejected_before_querying(self):
        with self.assertRaisesRegex(ValueError, "top_k"):
            search_speech(
                "query",
                config=self.config,
                runtime=self.runtime,
                top_k=0,
                storage=FakeStorage([]),
            )

    def test_old_metadata_requires_an_explicit_reindex(self):
        storage = FakeStorage(
            [
                {
                    "source_id": "0",
                    "raw_distance": 0.2,
                    "metadata": {"start": 1.0},
                }
            ]
        )
        with (
            patch(
                "vidxp.capabilities.speech.operations.speech_embedding",
                return_value=[0.5],
            ),
            self.assertRaisesRegex(IndexSchemaError, "must be rebuilt"),
        ):
            search_speech(
                "query",
                config=self.config,
                runtime=self.runtime,
                storage=storage,
            )

    def test_generic_serializer_keeps_empty_queries_and_is_deterministic(self):
        empty = SearchResult(
            query_id="q-empty",
            query="nothing",
            modality="speech",
            hits=(),
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "predictions.json"
            payload = serialize_predictions([empty], path)
            written = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(payload, {"q-empty": []})
        self.assertEqual(written, payload)

    def test_serializer_rejects_duplicate_query_ids(self):
        duplicate = SearchResult(
            query_id="q1",
            query="query",
            modality="speech",
            hits=(),
        )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            serialize_predictions([duplicate, duplicate])


if __name__ == "__main__":
    unittest.main()
