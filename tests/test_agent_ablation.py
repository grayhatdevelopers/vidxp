from __future__ import annotations

import json
from pathlib import Path

import pytest

from vidxp.benchmarks.agent_ablation_score import (
    interval_iou,
    score_ablation_boundary,
    score_temporal_grounding,
)
from vidxp.benchmarks.agent_ablation_tests import generate_tests


def test_interval_iou_matches_temporal_overlap() -> None:
    assert interval_iou(10, 20, 15, 25) == pytest.approx(1 / 3)
    assert interval_iou(0, 5, 6, 10) == 0


def test_temporal_grounding_reports_longvale_metrics() -> None:
    output = json.dumps(
        {
            "video_id": "video-1",
            "start_seconds": 10,
            "end_seconds": 20,
        }
    )
    result = score_temporal_grounding(
        output,
        {
            "vars": {
                "video_id": "video-1",
                "duration_seconds": 30,
                "expected_start": 15,
                "expected_end": 25,
            }
        },
    )

    assert result["pass"] is True
    assert result["namedScores"]["temporal_iou"] == pytest.approx(1 / 3)
    assert result["namedScores"]["r1_tiou_0_3"] == 1
    assert result["namedScores"]["r1_tiou_0_5"] == 0


def test_temporal_grounding_rejects_null_or_out_of_bounds_intervals() -> None:
    context = {
        "vars": {
            "video_id": "video-1",
            "duration_seconds": 30,
            "expected_start": 1,
            "expected_end": 2,
        }
    }
    null_result = score_temporal_grounding(
        '{"video_id":"video-1","start_seconds":null,"end_seconds":null}',
        context,
    )
    bounds_result = score_temporal_grounding(
        '{"video_id":"video-1","start_seconds":20,"end_seconds":31}',
        context,
    )

    assert null_result["pass"] is False
    assert bounds_result["pass"] is False


def test_ablation_boundary_requires_mcp_only_in_the_on_condition() -> None:
    trace = {
        "spans": [
            {
                "name": "MCP tool call",
                "attributes": {"tool.name": "mcp__vidxp__search_moments"},
            }
        ]
    }

    assert score_ablation_boundary(
        "{}", {"vars": {"expected_mcp": True}, "trace": trace}
    )["pass"]
    assert not score_ablation_boundary(
        "{}", {"vars": {"expected_mcp": False}, "trace": trace}
    )["pass"]


def test_ablation_boundary_rejects_direct_vidxp_cli_bypass() -> None:
    trace = {
        "spans": [
            {
                "name": "command",
                "attributes": {"command": "vidxp search sound alarm"},
            }
        ]
    }

    result = score_ablation_boundary(
        "{}", {"vars": {"expected_mcp": False}, "trace": trace}
    )

    assert result["pass"] is False
    assert "bypassed" in result["reason"]


def test_generator_pairs_each_manifest_task_across_conditions(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "tasks.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "id": "task-1",
                    "dataset": "example",
                    "video_id": "video-1",
                    "media_relpath": "media/video-1.mp4",
                    "duration_seconds": 10,
                    "event_index": 0,
                    "query": "an event",
                    "expected_start": 1,
                    "expected_end": 2,
                    "modalities": ["sound"],
                }
            ]
        ),
        encoding="utf-8",
    )

    tests = generate_tests(
        {
            "manifest": str(manifest),
            "providers": {"mcp_on": "on", "mcp_off": "off"},
        }
    )

    assert [test["providers"] for test in tests] == [["on"], ["off"]]
    assert [test["vars"]["expected_mcp"] for test in tests] == [True, False]


def test_committed_pilot_expands_to_ten_matched_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    benchmark = Path(__file__).parents[1] / "benchmarks" / "codex-mcp"
    monkeypatch.chdir(benchmark)

    tests = generate_tests(
        {
            "manifest": "tasks/longvale-part9-pilot.json",
            "providers": {"mcp_on": "on", "mcp_off": "off"},
        }
    )

    assert len(tests) == 20
    assert {test["metadata"]["condition"] for test in tests} == {
        "mcp-on",
        "mcp-off",
    }
    assert len({test["metadata"]["task_id"] for test in tests}) == 10
