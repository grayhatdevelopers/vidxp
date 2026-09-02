from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from modality_probe import _load_environment, _output_path
from vidxp.application_models import SearchHit, SearchResult
from vidxp.benchmarks.agent_ablation_score import interval_iou
from vidxp.benchmarks.point_to_span import (
    MINIMUM_PEAK_DISTANCE_SECONDS,
    NMS_TIOU_THRESHOLD,
    PAPER_URL,
    PEAK_PROMINENCE,
    TemporalSimilarity,
    adaptive_span_generator,
    squared_l2_to_cosine,
)
from vidxp.search_fusion import fuse_search_results


def _comparison_path(probe_path: Path, requested: Path | None) -> Path:
    if requested is not None:
        return requested.resolve()
    return probe_path.with_name(probe_path.name.replace(".probe.json", ".p2s.json"))


def _timeline_records(
    modality: str,
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    if modality == "sound":
        selected = [
            record
            for record in records
            if record["metadata"].get("representation") == "activation"
        ]
        return selected, "FineLAP activation records only"
    return records, "all indexed records"


def _metrics(
    start: float,
    end: float,
    *,
    expected_start: float,
    expected_end: float,
) -> dict[str, float]:
    return {
        "temporal_iou": interval_iou(start, end, expected_start, expected_end),
        "start_error_seconds": start - expected_start,
        "end_error_seconds": end - expected_end,
        "duration_error_seconds": (
            end - start - expected_end + expected_start
        ),
    }


def _generation_id(records: list[dict[str, Any]]) -> str:
    components = records[0]["source_id"].split(":")
    if len(components) < 4:
        raise ValueError("probe source ID does not contain an index generation")
    return components[0]


def compare_probe(
    probe_path: Path,
    output: Path | None = None,
) -> dict[str, Any]:
    probe = json.loads(probe_path.read_text(encoding="utf-8"))
    if probe.get("vector_distance") != "l2":
        raise ValueError("the current comparison requires squared L2 distances")
    expected_start = float(probe["expected_start"])
    expected_end = float(probe["expected_end"])
    output_top_k = int(probe["current_control"]["output_top_k"])
    modality_reports: dict[str, Any] = {}
    span_results = []
    started = time.perf_counter()

    for modality, modality_probe in probe["modalities"].items():
        source_records = modality_probe["records"]
        if modality == "speech":
            selected = sorted(
                source_records,
                key=lambda record: record["retrieval_rank"],
            )[:output_top_k]
            modality_reports[modality] = {
                "status": "passthrough",
                "reason": "speech records already contain semantic timestamps",
                "record_count": len(source_records),
                "candidates": [
                    {
                        "rank": record["retrieval_rank"],
                        "start_seconds": record["start_seconds"],
                        "end_seconds": record["end_seconds"],
                    }
                    for record in selected
                ],
            }
            span_results.append(
                SearchResult(
                    query_id=f"p2s-asg:speech:{probe['task_id']}",
                    query=probe["query"],
                    modality=modality,
                    hits=tuple(
                        SearchHit(
                            rank=rank,
                            media_id=probe["media_id"],
                            video_id=probe["media_id"],
                            generation_id=_generation_id(source_records),
                            start=record["start_seconds"],
                            end=record["end_seconds"],
                            score=record["ordering_score"],
                            raw_distance=record["raw_distance"],
                            modality=modality,
                            source_id=record["source_id"],
                            metadata=record["metadata"],
                        )
                        for rank, record in enumerate(selected, start=1)
                    ),
                )
            )
            continue
        records, selection = _timeline_records(modality, source_records)
        if not records:
            modality_reports[modality] = {
                "status": "not_applicable",
                "reason": selection,
            }
            continue
        sequence = tuple(
            TemporalSimilarity(
                start=float(record["start_seconds"]),
                end=float(record["end_seconds"]),
                similarity=squared_l2_to_cosine(record["raw_distance"]),
            )
            for record in records
        )
        result = adaptive_span_generator(sequence)
        candidates = [
            {
                "rank": rank,
                "start_seconds": candidate.start,
                "end_seconds": candidate.end,
                "score": candidate.score,
                "peak_time_seconds": candidate.peak_time,
                "peak_similarity": candidate.peak_similarity,
                "expansion_threshold": candidate.expansion_threshold,
            }
            for rank, candidate in enumerate(result.candidates, start=1)
        ]
        modality_reports[modality] = {
            "status": "ok" if candidates else "no_candidates",
            "record_selection": selection,
            "record_count": len(records),
            "sample_rate_hz": result.sample_rate_hz,
            "signal_standard_deviation": result.signal_standard_deviation,
            "adaptive_ratio": result.adaptive_ratio,
            "smoothing_window_samples": result.smoothing_window_samples,
            "candidates": candidates,
        }
        hits = tuple(
            SearchHit(
                rank=candidate["rank"],
                media_id=probe["media_id"],
                video_id=probe["media_id"],
                generation_id=_generation_id(source_records),
                start=candidate["start_seconds"],
                end=candidate["end_seconds"],
                score=candidate["score"],
                raw_distance=2.0 * (1.0 - candidate["score"]),
                modality=modality,
                source_id=f"p2s-asg:{modality}:{candidate['rank']}",
                metadata={"localizer": "p2s_asg_vidxp_v1"},
            )
            for candidate in candidates
        )
        span_results.append(
            SearchResult(
                query_id=f"p2s-asg:{modality}:{probe['task_id']}",
                query=probe["query"],
                modality=modality,
                hits=hits,
            )
        )

    fused = fuse_search_results(
        query=probe["query"],
        requested_modalities=tuple(result.modality for result in span_results),
        results=tuple(span_results),
        media_id=probe["media_id"],
        top_k=output_top_k,
        snapshot_id=probe["snapshot_id"],
    )
    top_moment = fused.moments[0] if fused.moments else None
    adapted_metrics = (
        _metrics(
            top_moment.start,
            top_moment.end,
            expected_start=expected_start,
            expected_end=expected_end,
        )
        if top_moment is not None
        else None
    )
    elapsed_seconds = time.perf_counter() - started
    payload = {
        "schema_version": 1,
        "task_id": probe["task_id"],
        "probe": str(probe_path.resolve()),
        "expected_interval": {
            "start_seconds": expected_start,
            "end_seconds": expected_end,
        },
        "method": {
            "id": "p2s_asg_vidxp_v1",
            "paper": PAPER_URL,
            "paper_component": (
                "Adaptive Span Generator, Section 3.1, with the published "
                "final NMS setting"
            ),
            "published_settings": {
                "peak_prominence": PEAK_PROMINENCE,
                "minimum_peak_distance_seconds": MINIMUM_PEAK_DISTANCE_SECONDS,
                "nms_tiou_threshold": NMS_TIOU_THRESHOLD,
            },
            "not_implemented": [
                "LLM query decomposition",
                "evidence-based reranking",
                "evidence-union injection",
            ],
            "vidxp_adaptations": [
                "use existing VideoPrism, SigLIP2, and FineLAP score curves",
                "convert normalized squared L2 distance to cosine similarity",
                "estimate each modality sample rate from record timestamps",
                "retain shorter FineLAP records at audio-window boundaries",
                "pass existing timestamped speech spans through unchanged",
                "round the paper's floating smoothing width to a sample count",
                "extend edge values during moving-average smoothing",
                "apply final NMS before fusion because later P2S stages are omitted",
                "fuse generated spans with VidXP reciprocal-rank fusion",
            ],
        },
        "model_calls": 0,
        "elapsed_seconds": elapsed_seconds,
        "current_control": probe["current_control"],
        "adaptation": {
            "modalities": modality_reports,
            "top_moment_metrics": adapted_metrics,
            "result": fused.model_dump(mode="json"),
        },
    }
    destination = _comparison_path(probe_path, output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    current_result = probe["current_control"]["result"]
    current_top = current_result["moments"][0] if current_result["moments"] else None
    return {
        "output": str(destination),
        "model_calls": 0,
        "elapsed_seconds": elapsed_seconds,
        "expected_interval": payload["expected_interval"],
        "current_control": {
            "top_interval": (
                {
                    "start_seconds": current_top["start"],
                    "end_seconds": current_top["end"],
                }
                if current_top is not None
                else None
            ),
            "metrics": probe["current_control"]["top_moment_metrics"],
        },
        "p2s_asg_adaptation": {
            "top_interval": (
                {
                    "start_seconds": top_moment.start,
                    "end_seconds": top_moment.end,
                }
                if top_moment is not None
                else None
            ),
            "metrics": adapted_metrics,
            "modality_candidate_counts": {
                name: len(report.get("candidates", []))
                for name, report in modality_reports.items()
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare the saved control with the P2S ASG adaptation."
    )
    parser.add_argument("task_id")
    parser.add_argument("--probe", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if arguments.probe is None:
        _load_environment()
        probe_path = _output_path(arguments.task_id, None)
    else:
        probe_path = arguments.probe.resolve()
    print(json.dumps(compare_probe(probe_path, arguments.output), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
