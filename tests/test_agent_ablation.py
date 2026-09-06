from __future__ import annotations

import json
from pathlib import Path

import pytest

from vidxp.benchmarks.agent_ablation_score import (
    event_coverage,
    interval_iou,
    score_ablation_boundary,
    score_temporal_grounding,
)
from vidxp.benchmarks.agent_ablation_tests import generate_tests


@pytest.fixture(autouse=True)
def _repository_machine_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIDXP_EVAL_MACHINE_ID", "test-machine-01")


def test_interval_iou_matches_temporal_overlap() -> None:
    assert interval_iou(10, 20, 15, 25) == pytest.approx(1 / 3)
    assert interval_iou(0, 5, 6, 10) == 0


def test_temporal_grounding_uses_bounded_chunk_hit_as_primary_score() -> None:
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
    assert result["score"] == pytest.approx(0.5)
    assert result["namedScores"]["bounded_chunk_hit"] == 1
    assert result["namedScores"]["event_coverage"] == pytest.approx(0.5)
    assert result["namedScores"]["chunk_duration_in_range"] == 1
    assert result["namedScores"]["temporal_iou"] == pytest.approx(1 / 3)
    assert result["namedScores"]["r1_tiou_0_3"] == 1
    assert result["namedScores"]["r1_tiou_0_5"] == 0


def test_event_coverage_is_normalized_to_one_practical_chunk() -> None:
    assert event_coverage(10, 20, 12, 14, target_chunk_seconds=10) == 1
    assert event_coverage(10, 20, 5, 25, target_chunk_seconds=10) == 1
    assert event_coverage(0, 8, 20, 22, target_chunk_seconds=10) == 0


def test_temporal_grounding_rejects_blink_and_whole_video_answers() -> None:
    context = {
        "vars": {
            "video_id": "video-1",
            "duration_seconds": 30,
            "expected_start": 10,
            "expected_end": 12,
        }
    }

    blink = score_temporal_grounding(
        '{"video_id":"video-1","start_seconds":10,"end_seconds":12}',
        context,
    )
    whole_video = score_temporal_grounding(
        '{"video_id":"video-1","start_seconds":0,"end_seconds":30}',
        context,
    )
    practical = score_temporal_grounding(
        '{"video_id":"video-1","start_seconds":8,"end_seconds":16}',
        context,
    )

    assert blink["pass"] is False
    assert blink["namedScores"]["event_coverage"] == 1
    assert blink["namedScores"]["chunk_duration_in_range"] == 0
    assert whole_video["pass"] is False
    assert whole_video["namedScores"]["event_coverage"] == 1
    assert whole_video["namedScores"]["chunk_duration_in_range"] == 0
    assert practical["pass"] is True
    assert practical["namedScores"]["bounded_chunk_hit"] == 1


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
            "allow_media_shell": False,
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
            "source_job_id": "baseline-source",
            "evidence": [{"evidence_id": "baseline-evidence"}],
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


def test_clean_user_rejects_host_developer_tool_paths() -> None:
    output = json.dumps({"source_job_id": None, "evidence": []})
    trace = {
        "spans": [
            {
                "name": "command",
                "attributes": {
                    "codex.command": "/opt/homebrew/bin/ffmpeg -i media/video.mp4"
                },
            }
        ]
    }

    result = score_ablation_boundary(
        output,
        {
            "vars": {"expected_vidxp": False, "forbid_host_tools": True},
            "trace": trace,
        },
    )

    assert result["pass"] is False
    assert "host developer-tool path" in result["reason"]


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
            "providers": {
                "vidxp_on": "on",
                "vidxp_off": "off",
                "clean_user": "clean",
            },
        }
    )

    assert [test["providers"] for test in tests] == [["on"], ["off"], ["clean"]]
    assert [test["vars"]["expected_vidxp"] for test in tests] == [
        True,
        False,
        False,
    ]
    assert [test["vars"]["allow_media_shell"] for test in tests] == [
        False,
        True,
        True,
    ]
    assert [test["vars"]["forbid_host_tools"] for test in tests] == [
        False,
        False,
        True,
    ]
    assert [test["vars"]["target_chunk_seconds"] for test in tests] == [10] * 3
    assert [test["vars"]["min_chunk_seconds"] for test in tests] == [8] * 3
    assert [test["vars"]["max_chunk_seconds"] for test in tests] == [12] * 3
    assert [test["vars"]["min_event_coverage"] for test in tests] == [0.5] * 3
    assert [test["vars"]["modalities"] for test in tests] == [
        '["sound"]',
        '["sound"]',
        '["sound"]',
    ]
    assert [test["metadata"]["modalities"] for test in tests] == [
        ["sound"],
        ["sound"],
        ["sound"],
    ]
    assert {test["metadata"]["machine_id"] for test in tests} == {
        "test-machine-01"
    }


def test_committed_manifest_expands_to_ten_matched_condition_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    benchmark = Path(__file__).parents[1] / "benchmarks" / "codex-mcp"
    monkeypatch.chdir(benchmark)

    tests = generate_tests(
        {
            "manifest": "tasks/longvale-part9-pilot.json",
            "providers": {
                "vidxp_on": "on",
                "vidxp_off": "off",
                "clean_user": "clean",
            },
        }
    )

    assert len(tests) == 30
    assert {test["metadata"]["condition"] for test in tests} == {
        "vidxp-on",
        "vidxp-off",
        "clean-user",
    }
    assert len({test["metadata"]["task_id"] for test in tests}) == 10

    prompt = (benchmark / "prompts" / "video-evidence.txt").read_text(
        encoding="utf-8"
    ).casefold()
    assert "vidxp" not in prompt
    assert "ffmpeg" not in prompt
    assert "condition" not in prompt


def test_pilot_uses_three_fresh_counterbalanced_repetitions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    benchmark = Path(__file__).parents[1] / "benchmarks" / "codex-mcp"
    monkeypatch.chdir(benchmark)
    monkeypatch.setenv("VIDXP_EVAL_MODE", "pilot")

    tests = generate_tests(
        {
            "manifest": "tasks/longvale-part9-pilot.json",
            "providers": {
                "vidxp_on": "on",
                "vidxp_off": "off",
                "clean_user": "clean",
            },
        }
    )

    assert len(tests) == 81
    assert all("retrieval_nonce" not in test["vars"] for test in tests)
    assert all("evidence_access" not in test["vars"] for test in tests)
    first_task_id = tests[0]["metadata"]["task_id"]
    first_task = [
        test for test in tests if test["metadata"]["task_id"] == first_task_id
    ]
    assert [test["metadata"]["condition"] for test in first_task] == [
        "vidxp-on",
        "vidxp-off",
        "clean-user",
        "vidxp-off",
        "clean-user",
        "vidxp-on",
        "clean-user",
        "vidxp-on",
        "vidxp-off",
    ]


def test_pilot_accepts_one_explicit_repetition_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    benchmark = Path(__file__).parents[1] / "benchmarks" / "codex-mcp"
    monkeypatch.chdir(benchmark)
    monkeypatch.setenv("VIDXP_EVAL_MODE", "pilot")
    monkeypatch.setenv("VIDXP_EVAL_REPETITIONS", "5")

    tests = generate_tests({"manifest": "tasks/longvale-part9-pilot.json"})

    assert len(tests) == 135
    assert {test["metadata"]["repetition"] for test in tests} == {1, 2, 3, 4, 5}
