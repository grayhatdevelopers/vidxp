import json
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pytest

from vidxp.benchmarks.aegbench import (
    load_aegbench,
    score_audio_event,
    spans_from_scores,
)
from vidxp.benchmarks.modality_gates import (
    load_charades_sta,
    load_finelap_grounding,
    load_finelap_retrieval,
    load_lighthouse_moments,
    load_msrvtt_queries,
)
from vidxp.benchmarks.modality_metrics import (
    RetrievalQuery,
    TemporalQuery,
    retrieval_metrics,
    temporal_retrieval_metrics,
)


def test_retrieval_metrics_score_complete_rankings() -> None:
    queries = [
        RetrievalQuery("q1", "first", ("a",)),
        RetrievalQuery("q2", "second", ("b",)),
    ]

    metrics = retrieval_metrics(
        queries,
        {"q1": ["a", "b"], "q2": ["a", "b"]},
    )

    assert metrics["recall_at_1"] == 0.5
    assert metrics["recall_at_5"] == 1.0
    assert metrics["recall_at_50"] == 1.0
    assert metrics["median_rank"] == 1.5


def test_temporal_metrics_keep_boundary_quality_at_two_depths() -> None:
    queries = [TemporalQuery("q1", "video", "event", ((4.0, 6.0),))]

    metrics = temporal_retrieval_metrics(
        queries,
        {"q1": [(0.0, 2.0), (4.0, 6.0)]},
    )

    assert metrics["recall_at_1_tiou_0_5"] == 0.0
    assert metrics["recall_at_5_tiou_0_7"] == 1.0


def test_msrvtt_loader_uses_only_the_declared_gallery() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        annotations = root / "annotations.json"
        gallery = root / "gallery.txt"
        annotations.write_text(
            json.dumps(
                {
                    "sentences": [
                        {"sen_id": 1, "video_id": "video0", "caption": "zero"},
                        {"sen_id": 2, "video_id": "video1", "caption": "one"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        gallery.write_text("video1\n", encoding="utf-8")

        media_ids, queries = load_msrvtt_queries(annotations, gallery)

    assert media_ids == ["video1"]
    assert queries == [RetrievalQuery("2", "one", ("video1",))]


def test_charades_and_lighthouse_load_official_interval_shapes() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        charades = root / "charades.txt"
        lighthouse = root / "moments.jsonl"
        charades.write_text("VID1 1.5 3.5##person opens a door\n", encoding="utf-8")
        lighthouse.write_text(
            json.dumps(
                {
                    "qid": "q1",
                    "vid": "audio1",
                    "query": "a bell rings",
                    "relevant_windows": [[2, 4], [8, 9]],
                }
            )
            + "\n",
            encoding="utf-8",
        )

        action = load_charades_sta(charades)
        sound = load_lighthouse_moments(lighthouse)

    assert action[0].intervals == ((1.5, 3.5),)
    assert sound[0].intervals == ((2.0, 4.0), (8.0, 9.0))


def test_finelap_retrieval_requires_five_captions() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        audio = root / "sample.wav"
        metadata = root / "metadata.jsonl"
        audio.write_bytes(b"audio")
        metadata.write_text(
            json.dumps(
                {
                    "audio_id": audio.name,
                    "audio_path": audio.name,
                    "caption": ["only one"],
                }
            )
            + "\n",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="exactly five captions"):
            load_finelap_retrieval(metadata)


def test_finelap_grounding_keeps_repeated_event_intervals() -> None:
    with TemporaryDirectory() as directory:
        metadata = Path(directory) / "grounding.json"
        metadata.write_text(
            json.dumps(
                [
                    {
                        "audiocap_id": 7,
                        "audio_id": "sample.wav",
                        "phrases": [
                            {
                                "phrase": "a bell rings",
                                "start_index": 2,
                                "segments": [[1, 2], [5, 6]],
                            }
                        ],
                    }
                ]
            ),
            encoding="utf-8",
        )

        queries = load_finelap_grounding(metadata)

    assert queries[0].intervals == ((1.0, 2.0), (5.0, 6.0))


def test_aegbench_loader_preserves_repeats_and_flags_missing_intervals() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        audio = root / "sample.wav"
        audio.write_bytes(b"audio")
        metadata = root / "manifest.json"
        metadata.write_text(
            json.dumps(
                {
                    "items": [
                        {
                            "id": "sample",
                            "audio_path": audio.name,
                            "duration": 10,
                            "categories": ["bell", "unlabelled"],
                            "clips": [
                                {"category": "bell", "start": 1, "end": 2},
                                {"category": "bell", "start": 5, "end": 6},
                            ],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        items, _metadata = load_aegbench(metadata)

    assert items[0].queries[0].intervals == ((1.0, 2.0), (5.0, 6.0))
    assert items[0].excluded_categories == ("unlabelled",)


def test_audio_event_metrics_separate_ranking_from_default_threshold() -> None:
    starts = np.asarray([0.0, 1.0, 2.0, 3.0])
    ends = starts + 1.0
    scores = np.asarray([0.1, 0.9, 0.8, 0.2])

    spans = spans_from_scores(scores, starts, ends, threshold=0.5)
    metrics = score_audio_event(
        scores,
        starts,
        ends,
        intervals=((1.0, 3.0),),
        duration=4.0,
        threshold=0.5,
    )

    assert spans == [(1.0, 3.0)]
    assert metrics["frame_auc"] == 1.0
    assert metrics["frame_average_precision"] == 1.0
    assert metrics["mean_iou"] == 1.0


def test_audio_event_average_precision_does_not_favor_tie_order() -> None:
    metrics = score_audio_event(
        np.asarray([0.9, 0.9, 0.1]),
        np.asarray([0.0, 1.0, 2.0]),
        np.asarray([1.0, 2.0, 3.0]),
        intervals=((0.0, 1.0),),
        duration=3.0,
        threshold=0.5,
    )

    assert metrics["frame_average_precision"] == 0.5
