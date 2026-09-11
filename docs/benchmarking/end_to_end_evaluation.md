# End-to-end retrieval evaluation

Collection index: [Benchmarking research](README.md)

The dataset adapters ([DiDeMo](adapter_validation.md), HiREST) score one
modality against an official evaluator through the benchmark-ready core. This
harness is complementary: it drives the same public `search` operation a real
client calls and reports, in one place, how the complete product path behaves
on a labeled set. It does not replace the published-dataset baselines and does
not claim a published score.

The harness lives in `vidxp.benchmarks.end_to_end`.

## What it measures

Each measure maps to one line of the request in issue #76:

| Measure | Metric |
|---|---|
| Whether relevant moments are found | `recall_at_k` at a relevance IoU (default 0.5) |
| Timestamp and temporal-range accuracy | `mean_top1_iou`, plus per-case `best_iou` |
| Ranking quality across modalities | `mean_reciprocal_rank`, `ndcg_at_k` (IoU-graded), `modality_contribution` |
| Whether returned evidence supports the result | `evidence_support_rate` from delivered evidence ranges |
| Latency, failures, degraded/partial results | `latency_ms_*`, `failed_cases`, `no_result_cases`, per-case `evidence_degraded` |

Ranking scores are ordering-only: they sort results within one response and are
not probabilities. Calibrated scoring is a separate concern (see #90).

## How it works

The evaluator is dependency-injected. A caller supplies a `SearchFn` that maps
an `EvaluationCase` to a public `FusedSearchResult`. In tests this is a
deterministic fake, so every metric is exercised without loading models. In
production, bind the real public operation with `application_search_fn`:

```python
from vidxp.benchmarks.end_to_end import (
    EvaluationDataset,
    application_search_fn,
    evaluate_end_to_end,
)

dataset = EvaluationDataset.model_validate_json(
    labeled_cases_path.read_text(encoding="utf-8")
)

search_fn = application_search_fn(
    application.search,          # the public VidXPApplication.search
    snapshot=snapshot_reference, # pin a snapshot for reproducibility
    top_k=10,
)

report = evaluate_end_to_end(dataset, search_fn, relevance_iou=0.5)
report_path.write_text(
    report.model_dump_json(indent=2), encoding="utf-8"
)
```

Because `search_fn` calls `VidXPApplication.search` with the same
`SearchCommand` a client sends, the harness measures the public product path
rather than an internal shortcut. Passing an `evidence_delivery` policy to
`application_search_fn` also exercises the evidence path, which enables
`evidence_support_rate`.

A case whose search fails is recorded as failed and excluded from the quality
metrics, so one broken query never hides the rest.

## Dataset shape

```json
{
  "name": "smoke",
  "cases": [
    {
      "case_id": "taxi-night",
      "query": "a taxi at night",
      "media_id": "…optional single-media scope…",
      "modalities": ["scene", "speech"],
      "relevant": [
        {"media_id": "…", "start": 12.0, "end": 18.5}
      ]
    }
  ]
}
```

## Scope

This change adds the transport-neutral evaluator and its metrics. Wiring it to
a CLI, HTTP, or MCP surface is intentionally a follow-up so the tested core
lands first; `application_search_fn` already shows the exact binding a surface
adapter needs.
