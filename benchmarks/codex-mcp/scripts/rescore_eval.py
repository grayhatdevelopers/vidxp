from __future__ import annotations

import json
import sys
from typing import Any

from vidxp.benchmarks.agent_ablation_score import (
    score_ablation_boundary,
    score_temporal_grounding,
)


def main() -> None:
    records: list[dict[str, Any]] = json.load(sys.stdin)
    rescored = []
    for record in records:
        context = {
            "vars": record.get("vars", {}),
            "metadata": record.get("metadata", {}),
            "trace": {"spans": record.get("spans", [])},
        }
        output = record.get("output", "")
        rescored.append(
            {
                "test_idx": record.get("test_idx"),
                "temporal": score_temporal_grounding(output, context),
                "boundary": score_ablation_boundary(output, context),
            }
        )
    json.dump(rescored, sys.stdout, separators=(",", ":"))


if __name__ == "__main__":
    main()
