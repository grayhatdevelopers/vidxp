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
        }
    json.dump(traces, sys.stdout, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
