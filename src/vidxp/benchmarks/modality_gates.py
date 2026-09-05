from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TypeVar

from vidxp.benchmarks.indexed_modality import (
    input_artifact,
    resolve_media,
    run_indexed_retrieval,
    run_indexed_temporal,
)
from vidxp.benchmarks.modality_metrics import RetrievalQuery, TemporalQuery
from vidxp.capabilities.action.operations import search_videoprism
from vidxp.capabilities.sound.operations import (
    GLOBAL_REPRESENTATION,
    LOCAL_REPRESENTATION,
    search_sound,
)


MSRVTT_SOURCE = "https://github.com/m-bain/frozen-in-time"
CHARADES_SOURCE = "https://github.com/jiyanggao/TALL"
FINELAP_SOURCE = "https://github.com/xiquan-li/FineLAP"
LIGHTHOUSE_SOURCE = "https://github.com/line/lighthouse"
_T = TypeVar("_T")


def _selected(
    items: Sequence[_T],
    indices: Sequence[int] | None,
) -> list[_T]:
    if indices is None:
        return list(items)
    selected = []
    seen = set()
    for index in indices:
        if index in seen:
            raise ValueError(f"Duplicate subset index: {index}")
        if index < 0 or index >= len(items):
            raise IndexError(f"Subset index out of range: {index}")
        seen.add(index)
        selected.append(items[index])
    if not selected:
        raise ValueError("A benchmark subset must not be empty.")
    return selected


def _json_list_or_lines(path: str | Path) -> list[str]:
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        values = [line.strip() for line in text.splitlines() if line.strip()]
    else:
        if not isinstance(payload, list):
            raise ValueError("The gallery split must be a JSON list or text lines.")
        values = [str(value).strip() for value in payload]
    if not values or any(not value for value in values):
        raise ValueError("The gallery split must contain media IDs.")
    if len(values) != len(set(values)):
        raise ValueError("The gallery split contains duplicate media IDs.")
    return values


def load_msrvtt_queries(
    annotations_path: str | Path,
    gallery_path: str | Path,
) -> tuple[list[str], list[RetrievalQuery]]:
    payload = json.loads(Path(annotations_path).read_text(encoding="utf-8"))
    sentences = payload.get("sentences") if isinstance(payload, Mapping) else None
    if not isinstance(sentences, list) or not sentences:
        raise ValueError("MSR-VTT annotations require a non-empty sentences list.")
    gallery = _json_list_or_lines(gallery_path)
    gallery_set = set(gallery)
    queries = []
    for index, sentence in enumerate(sentences):
        if not isinstance(sentence, Mapping):
            raise ValueError(f"MSR-VTT sentence {index} must be an object.")
        media_id = str(sentence.get("video_id", "")).strip()
        if media_id not in gallery_set:
            continue
        text = str(sentence.get("caption", "")).strip()
        query_id = str(sentence.get("sen_id", f"sentence-{index}"))
        if not text:
            raise ValueError(f"MSR-VTT sentence {index} has no caption.")
        queries.append(RetrievalQuery(query_id, text, (media_id,)))
    if not queries:
        raise ValueError("No MSR-VTT captions match the selected gallery.")
    missing = gallery_set - {query.relevant_media_ids[0] for query in queries}
    if missing:
        raise ValueError(
            "MSR-VTT gallery videos lack captions: " + ", ".join(sorted(missing))
        )
    return gallery, queries


def load_charades_sta(path: str | Path) -> list[TemporalQuery]:
    queries = []
    for line_number, raw_line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line:
            continue
        try:
            interval, text = line.split("##", 1)
            media_id, start, end = interval.split()
            start_seconds = float(start)
            end_seconds = float(end)
        except ValueError as exc:
            raise ValueError(
                f"Invalid Charades-STA annotation on line {line_number}."
            ) from exc
        queries.append(
            TemporalQuery(
                query_id=f"charades-{line_number}",
                media_id=media_id,
                text=text.strip(),
                intervals=((start_seconds, end_seconds),),
            )
        )
    if not queries:
        raise ValueError("Charades-STA annotations must not be empty.")
    return queries


def load_finelap_retrieval(
    path: str | Path,
) -> tuple[dict[str, Path], list[RetrievalQuery]]:
    metadata = Path(path)
    media: dict[str, Path] = {}
    queries = []
    for line_number, raw_line in enumerate(
        metadata.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not raw_line.strip():
            continue
        item = json.loads(raw_line)
        audio_id = str(item.get("audio_id", "")).strip()
        audio_path = Path(str(item.get("audio_path", "")))
        captions = item.get("caption")
        if not audio_id or not isinstance(captions, list) or len(captions) != 5:
            raise ValueError(
                f"FineLAP retrieval line {line_number} requires an audio_id "
                "and exactly five captions."
            )
        if not audio_path.is_absolute():
            audio_path = metadata.parent / audio_path
        if not audio_path.is_file():
            raise FileNotFoundError(f"FineLAP audio not found: {audio_path}")
        if audio_id in media:
            raise ValueError(f"Duplicate FineLAP audio_id: {audio_id}")
        media[audio_id] = audio_path.resolve()
        for caption_index, caption in enumerate(captions, start=1):
            text = str(caption).strip()
            if not text:
                raise ValueError(
                    f"FineLAP caption {caption_index} for {audio_id} is empty."
                )
            queries.append(
                RetrievalQuery(
                    f"{audio_id}:caption-{caption_index}",
                    text,
                    (audio_id,),
                )
            )
    if not media:
        raise ValueError("FineLAP retrieval metadata must not be empty.")
    return media, queries


def load_finelap_grounding(path: str | Path) -> list[TemporalQuery]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("FineLAP grounding metadata must be a non-empty list.")
    queries = []
    for item_index, item in enumerate(payload):
        if not isinstance(item, Mapping):
            raise ValueError(f"FineLAP grounding item {item_index} is invalid.")
        audio_id = str(item.get("audio_id", "")).strip()
        phrases = item.get("phrases")
        if not audio_id or not isinstance(phrases, list):
            raise ValueError(
                f"FineLAP grounding item {item_index} requires audio_id and phrases."
            )
        for phrase_index, phrase in enumerate(phrases):
            segments = phrase.get("segments") if isinstance(phrase, Mapping) else None
            text = (
                str(phrase.get("phrase", "")).strip()
                if isinstance(phrase, Mapping)
                else ""
            )
            if not text or not isinstance(segments, list) or not segments:
                raise ValueError(
                    f"FineLAP phrase {item_index}/{phrase_index} is invalid."
                )
            queries.append(
                TemporalQuery(
                    query_id=(
                        f"{item.get('audiocap_id', item_index)}:"
                        f"{phrase.get('start_index', phrase_index)}"
                    ),
                    media_id=audio_id,
                    text=text,
                    intervals=tuple(
                        (float(segment[0]), float(segment[1]))
                        for segment in segments
                        if tuple(segment) != (0, 0)
                    ),
                )
            )
    if not queries:
        raise ValueError("FineLAP grounding metadata contains no phrases.")
    return queries


def load_lighthouse_moments(path: str | Path) -> list[TemporalQuery]:
    queries = []
    for line_number, raw_line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not raw_line.strip():
            continue
        item = json.loads(raw_line)
        try:
            query_id = str(item["qid"])
            media_id = str(item["vid"])
            text = str(item["query"])
            intervals = tuple(
                (float(window[0]), float(window[1]))
                for window in item["relevant_windows"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"Invalid Lighthouse moment record on line {line_number}."
            ) from exc
        queries.append(TemporalQuery(query_id, media_id, text, intervals))
    if not queries:
        raise ValueError("Lighthouse moment metadata must not be empty.")
    return queries


def _media_for_queries(
    media_directory: str | Path,
    queries: Sequence[TemporalQuery],
    *,
    extensions: Sequence[str],
) -> dict[str, Path]:
    return {
        media_id: resolve_media(
            media_directory,
            media_id,
            extensions=extensions,
        )
        for media_id in sorted({query.media_id for query in queries})
    }


def run_msrvtt_action(
    *,
    annotations_path: str | Path,
    gallery_path: str | Path,
    media_directory: str | Path,
    run_id: str,
    query_indices: Sequence[int] | None = None,
    output_root: str | Path = "benchmark_runs",
    device: str = "cpu",
    reset: bool = False,
) -> dict[str, Any]:
    gallery, all_queries = load_msrvtt_queries(annotations_path, gallery_path)
    queries = _selected(all_queries, query_indices)
    media = {
        media_id: resolve_media(
            media_directory,
            media_id,
            extensions=(".mp4", ".webm", ".mkv", ".avi"),
        )
        for media_id in gallery
    }
    return run_indexed_retrieval(
        benchmark="msrvtt-1k-a",
        split="test",
        modality="action",
        media=media,
        queries=queries,
        search=search_videoprism,
        run_id=run_id,
        artifacts=(
            input_artifact(
                annotations_path,
                name="MSR-VTT annotations",
                source=MSRVTT_SOURCE,
            ),
            input_artifact(
                gallery_path,
                name="MSR-VTT 1K-A gallery",
                source=MSRVTT_SOURCE,
            ),
        ),
        output_root=output_root,
        device=device,
        reset=reset,
        result_classification=(
            "current_provider_full_native_retrieval"
            if query_indices is None and len(gallery) == 1000
            else "current_provider_native_retrieval_subset"
        ),
    )


def run_charades_action(
    *,
    annotations_path: str | Path,
    media_directory: str | Path,
    run_id: str,
    query_indices: Sequence[int] | None = None,
    output_root: str | Path = "benchmark_runs",
    device: str = "cpu",
    reset: bool = False,
) -> dict[str, Any]:
    queries = _selected(load_charades_sta(annotations_path), query_indices)
    return run_indexed_temporal(
        benchmark="charades-sta",
        split="test",
        modality="action",
        media=_media_for_queries(
            media_directory,
            queries,
            extensions=(".mp4", ".webm", ".mkv", ".avi"),
        ),
        queries=queries,
        search=search_videoprism,
        run_id=run_id,
        artifacts=(
            input_artifact(
                annotations_path,
                name="Charades-STA annotations",
                source=CHARADES_SOURCE,
            ),
        ),
        output_root=output_root,
        device=device,
        reset=reset,
        result_classification=(
            "current_provider_product_temporal_result"
            if query_indices is None
            else "current_provider_product_temporal_subset"
        ),
    )


def run_finelap_retrieval(
    *,
    metadata_path: str | Path,
    run_id: str,
    entry_indices: Sequence[int] | None = None,
    output_root: str | Path = "benchmark_runs",
    device: str = "cpu",
    reset: bool = False,
) -> dict[str, Any]:
    all_media, all_queries = load_finelap_retrieval(metadata_path)
    media_ids = list(all_media)
    selected_ids = _selected(media_ids, entry_indices)
    selected_set = set(selected_ids)
    media = {media_id: all_media[media_id] for media_id in selected_ids}
    queries = [
        query
        for query in all_queries
        if query.relevant_media_ids[0] in selected_set
    ]
    return run_indexed_retrieval(
        benchmark="finelap-retrieval",
        split="test",
        modality="sound",
        media=media,
        queries=queries,
        search=search_sound,
        search_filters={"representation": GLOBAL_REPRESENTATION},
        run_id=run_id,
        artifacts=(
            input_artifact(
                metadata_path,
                name="FineLAP retrieval metadata",
                source=FINELAP_SOURCE,
            ),
        ),
        output_root=output_root,
        device=device,
        reset=reset,
        result_classification=(
            "current_provider_native_clip_retrieval"
            if entry_indices is None
            else "current_provider_native_clip_retrieval_subset"
        ),
    )


def run_finelap_grounding(
    *,
    metadata_path: str | Path,
    audio_directory: str | Path,
    run_id: str,
    query_indices: Sequence[int] | None = None,
    output_root: str | Path = "benchmark_runs",
    device: str = "cpu",
    reset: bool = False,
) -> dict[str, Any]:
    queries = _selected(load_finelap_grounding(metadata_path), query_indices)
    return run_indexed_temporal(
        benchmark="tag-grounding",
        split="test",
        modality="sound",
        media=_media_for_queries(
            audio_directory,
            queries,
            extensions=(".wav", ".flac", ".mp3", ".m4a"),
        ),
        queries=queries,
        search=search_sound,
        search_filters={"representation": LOCAL_REPRESENTATION},
        run_id=run_id,
        artifacts=(
            input_artifact(
                metadata_path,
                name="TAG grounding metadata",
                source=FINELAP_SOURCE,
            ),
        ),
        output_root=output_root,
        device=device,
        reset=reset,
        result_classification=(
            "current_provider_native_dense_ranking_diagnostic"
            if query_indices is None
            else "current_provider_native_dense_ranking_subset"
        ),
    )


def run_finelap_audio_moment(
    *,
    metadata_path: str | Path,
    audio_directory: str | Path,
    run_id: str,
    dataset: str,
    query_indices: Sequence[int] | None = None,
    output_root: str | Path = "benchmark_runs",
    device: str = "cpu",
    reset: bool = False,
) -> dict[str, Any]:
    queries = _selected(load_lighthouse_moments(metadata_path), query_indices)
    return run_indexed_temporal(
        benchmark=dataset,
        split="test",
        modality="sound",
        media=_media_for_queries(
            audio_directory,
            queries,
            extensions=(".wav", ".flac", ".mp3", ".m4a"),
        ),
        queries=queries,
        search=search_sound,
        run_id=run_id,
        artifacts=(
            input_artifact(
                metadata_path,
                name=f"{dataset} moment metadata",
                source=LIGHTHOUSE_SOURCE,
            ),
        ),
        output_root=output_root,
        device=device,
        reset=reset,
        result_classification=(
            "current_provider_product_audio_moment_result"
            if query_indices is None
            else "current_provider_product_audio_moment_subset"
        ),
    )
