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


def _ablation_fixture() -> tuple[str, dict, dict]:
    job_id = "job-1"
    evidence_id = "evidence-1"
    output = json.dumps(
        {
            "video_id": "video-1",
            "answer": "The event occurs.",
            "start_seconds": 10,
            "end_seconds": 20,
            "modalities": ["sound"],
            "source_job_id": job_id,
            "evidence": [
                {
                    "evidence_id": evidence_id,
                    "start_seconds": 10,
                    "end_seconds": 20,
                    "modality": "sound",
                    "description": "The event is audible.",
                }
            ],
        }
    )
    context = {
        "vars": {
            "expected_vidxp": True,
            "video_id": "video-1",
            "media_relpath": "media/video-1.mp4",
            "query": "the event",
            "modalities": '["sound"]',
        },
        "trace": {
            "spans": [
                {
                    "name": "exec /bin/zsh",
                    "attributes": {
                        "promptfoo.skill.name": "vidxp-find-video-evidence",
                        "promptfoo.skill.path": (
                            "/eval/workspace/vidxp-on/.agents/skills/"
                            "vidxp-find-video-evidence/SKILL.md"
                        ),
                        "codex.command": (
                            "sed -n 1,240p "
                            ".agents/skills/vidxp-find-video-evidence/SKILL.md"
                        ),
                    },
                },
                _tool_span("get_workspace", {"filename": "video-1.mp4"}),
                _tool_span("list_media", {"filename": "video-1.mp4"}),
                _tool_span(
                    "search_moments",
                    {
                        "idempotency_key": "fresh-search-0001",
                        "command": {
                            "media_id": "media-1",
                            "query": "the event",
                            "modalities": ["scene", "sound"],
                            "evidence_delivery": {
                                "mode": "keyframes_and_clips",
                                "max_items": 3,
                            },
                        }
                    },
                ),
                _tool_span("wait_job", {"job_id": job_id}),
                _tool_span("get_job_evidence", {"job_id": job_id}),
            ]
        },
    }
    job = {
        "job_id": job_id,
        "kind": "search",
        "state": "succeeded",
        "created_at": "2026-09-02T00:00:10Z",
        "result": {
            "kind": "search",
            "result": {
                "query": "the event",
                "moments": [
                    {
                        "hits": [
                            {
                                "media_id": "media-1",
                                "video_id": "video-1",
                            }
                        ]
                    }
                ],
                "evidence_delivery": {
                    "policy": {
                        "mode": "keyframes_and_clips",
                        "max_items": 3,
                    },
                    "items": [
                        {
                            "evidence_id": evidence_id,
                            "media_id": "media-1",
                            "modalities": ["scene", "sound"],
                            "state": "ready",
                            "range": {
                                "source_start_seconds": 9,
                                "source_end_seconds": 21,
                            },
                        }
                    ],
                },
            },
        },
    }
    return output, context, job


def _tool_span(name: str, arguments: dict) -> dict:
    return {
        "name": f"mcp vidxp/{name}",
        "startTime": 1_788_307_200_000_000_000,
        "attributes": {
            "codex.mcp.server": "vidxp",
            "codex.mcp.tool": name,
            "codex.mcp.input": json.dumps(arguments),
        },
    }


def test_ablation_boundary_attests_successful_vidxp_evidence_job() -> None:
    output, context, job = _ablation_fixture()

    result = score_ablation_boundary(
        output,
        context,
        job_loader=lambda _job_id: job,
    )

    assert result["pass"] is True


def test_ablation_boundary_rejects_failed_job_or_shell_fallback() -> None:
    output, context, job = _ablation_fixture()
    failed_job = {**job, "state": "failed", "result": None}
    failed = score_ablation_boundary(
        output,
        context,
        job_loader=lambda _job_id: failed_job,
    )
    context["trace"]["spans"].append(
        {
            "name": "exec /bin/zsh",
            "attributes": {"codex.command": "ffmpeg -i media/video-1.mp4"},
        }
    )
    fallback = score_ablation_boundary(
        output,
        context,
        job_loader=lambda _job_id: job,
    )

    assert failed["pass"] is False
    assert "did not succeed" in failed["reason"]
    assert fallback["pass"] is False
    assert "through the shell" in fallback["reason"]


def test_ablation_boundary_rejects_job_from_an_earlier_trace() -> None:
    output, context, job = _ablation_fixture()
    stale_job = {**job, "created_at": "2026-09-01T23:59:59Z"}

    result = score_ablation_boundary(
        output,
        context,
        job_loader=lambda _job_id: stale_job,
    )

    assert result["pass"] is False
    assert "predates" in result["reason"]


def test_ablation_boundary_accepts_isolated_baseline() -> None:
    output = json.dumps(
        {
            "source_job_id": None,
            "evidence": [{"evidence_id": None}],
        }
    )
    trace = {"spans": [{"name": "agent response", "attributes": {}}]}

    result = score_ablation_boundary(
        output,
        {"vars": {"expected_vidxp": False}, "trace": trace},
    )

    assert result["pass"] is True


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
        "{}", {"vars": {"expected_vidxp": False}, "trace": trace}
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
            "providers": {"vidxp_on": "on", "vidxp_off": "off"},
        }
    )

    assert [test["providers"] for test in tests] == [["on"], ["off"]]
    assert [test["vars"]["expected_vidxp"] for test in tests] == [True, False]
    assert [test["vars"]["modalities"] for test in tests] == [
        '["sound"]',
        '["sound"]',
    ]
    assert [test["metadata"]["modalities"] for test in tests] == [
        ["sound"],
        ["sound"],
    ]


def test_committed_pilot_expands_to_ten_matched_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    benchmark = Path(__file__).parents[1] / "benchmarks" / "codex-mcp"
    monkeypatch.chdir(benchmark)

    tests = generate_tests(
        {
            "manifest": "tasks/longvale-part9-pilot.json",
            "providers": {"vidxp_on": "on", "vidxp_off": "off"},
        }
    )

    assert len(tests) == 20
    assert {test["metadata"]["condition"] for test in tests} == {
        "vidxp-on",
        "vidxp-off",
    }
    assert len({test["metadata"]["task_id"] for test in tests}) == 10
