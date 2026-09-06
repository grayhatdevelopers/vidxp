from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from typing import Any

from vidxp.benchmarks.agent_ablation_score import _load_durable_job


def _retrieval_payload(job: Mapping[str, Any]) -> Mapping[str, Any]:
    wrapper = job.get("result")
    payload = wrapper.get("result") if isinstance(wrapper, Mapping) else None
    if not isinstance(payload, Mapping):
        raise ValueError("job has no typed retrieval result")
    return payload


def _surface_candidates(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    delivery = payload.get("evidence_delivery")
    if not isinstance(delivery, Mapping):
        return []
    board = delivery.get("board")
    candidates = board.get("tiles") if isinstance(board, Mapping) else None
    if not isinstance(candidates, list):
        candidates = delivery.get("items")
    if not isinstance(candidates, list):
        return []

    surfaced: list[dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, Mapping):
            continue
        source_range = item.get("range")
        if isinstance(source_range, Mapping):
            start = source_range.get("source_start_seconds")
            end = source_range.get("source_end_seconds")
        else:
            start = item.get("start")
            end = item.get("end")
        surfaced.append(
            {
                "evidence_id": item.get("evidence_id"),
                "rank": item.get("rank"),
                "start": start,
                "end": end,
                "modalities": item.get("modalities", []),
                "state": item.get("state"),
            }
        )
    return surfaced


def main() -> int:
    job_ids = tuple(dict.fromkeys(sys.argv[1:]))
    if not job_ids:
        raise SystemExit("usage: retrieval_trace.py JOB_ID [JOB_ID ...]")

    traces: dict[str, Mapping[str, Any]] = {}
    for job_id in job_ids:
        job = _load_durable_job(job_id)
        payload = _retrieval_payload(job)
        traces[job_id] = {
            "query": payload.get("query", payload.get("question")),
            "moments": payload.get("moments", []),
            "surface_candidates": _surface_candidates(payload),
        }
    json.dump(traces, sys.stdout, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
