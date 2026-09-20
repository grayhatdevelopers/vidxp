import json
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid1, uuid4

from pydantic import ValidationError

from vidxp.core.contracts import (
    INDEX_SCHEMA_VERSION,
    MANIFEST_SCHEMA_VERSION,
)
from vidxp.core.generations import CompletedGenerationManifest


SHA256 = "a" * 64
OTHER_SHA256 = "b" * 64


def completed_manifest() -> dict:
    generation_id = uuid4().hex
    created_at = datetime(2026, 7, 29, tzinfo=timezone.utc).isoformat()
    completed_at = datetime(2026, 7, 29, 0, 1, tzinfo=timezone.utc).isoformat()
    return {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "index_schema_version": INDEX_SCHEMA_VERSION,
        "dataset": "local",
        "split": "local",
        "run_id": "default",
        "generation_id": generation_id,
        "state": "complete",
        "created_at": created_at,
        "updated_at": completed_at,
        "completed_at": completed_at,
        "config_fingerprint": SHA256,
        "execution_fingerprint": OTHER_SHA256,
        "configuration": {
            "enabled_modalities": ["scene", "speech"],
            "frame_stride": 1,
        },
        "models": {"runtime": {"requested": "cpu"}},
        "git": {"commit": None, "dirty": None},
        "environment": {
            "python": "3.13",
            "dependencies": {"chromadb": "1.5.9"},
        },
        "inputs": {
            "episode-1": {
                "sha256": SHA256,
                "checksums": {"video": SHA256},
                "size": 1024,
                "source_name": "episode.mp4",
                "path": "/media/episode.mp4",
                "metadata": {},
            }
        },
        "videos": {
            "episode-1": {
                "state": "complete",
                "started_at": created_at,
                "stages": {
                    "scene": {
                        "video_id": "episode-1",
                        "stage": "scene",
                        "seconds": 1.5,
                        "stats": {"scene_frames": 4},
                        "recorded_at": completed_at,
                    }
                },
                "completed_at": completed_at,
                "summary": {"processed_frames": 4},
            }
        },
        "completed_videos": ["episode-1"],
        "failed_videos": [],
        "interrupted_videos": [],
        "processed_frames": 4,
        "record_counts": {"scene": 4, "speech": 2},
        "store_size_bytes_at_commit": 4096,
    }


class CompletedGenerationManifestTests(unittest.TestCase):
    def validate(self, payload: dict) -> CompletedGenerationManifest:
        return CompletedGenerationManifest.model_validate_json(
            json.dumps(payload)
        )

    def test_valid_completed_manifest_is_strict_frozen_and_json_safe(self):
        manifest = self.validate(completed_manifest())

        self.assertEqual(manifest.state, "complete")
        self.assertEqual(manifest.record_counts["scene"], 4)
        with self.assertRaises(ValidationError):
            manifest.state = "running"

        invalid = completed_manifest()
        invalid["unexpected"] = True
        with self.assertRaises(ValidationError):
            self.validate(invalid)

        invalid = completed_manifest()
        invalid["store_size_bytes_at_commit"] = "4096"
        with self.assertRaises(ValidationError):
            CompletedGenerationManifest.model_validate(invalid)

        invalid = completed_manifest()
        invalid["models"] = {"runtime": object()}
        with self.assertRaises(ValidationError):
            CompletedGenerationManifest.model_validate(invalid)

    def test_requires_versions_uuid4_complete_state_and_aware_timestamps(self):
        invalid_values = (
            ("manifest_schema_version", MANIFEST_SCHEMA_VERSION + 1),
            ("index_schema_version", INDEX_SCHEMA_VERSION + 1),
            ("generation_id", uuid1().hex),
            ("generation_id", str(uuid4())),
            ("state", "running"),
            ("completed_at", "2026-07-29T00:01:00"),
        )
        for field, value in invalid_values:
            with self.subTest(field=field, value=value):
                payload = completed_manifest()
                payload[field] = value
                with self.assertRaises(ValidationError):
                    self.validate(payload)

    def test_requires_one_consistent_completed_media_without_failures(self):
        mutations = []

        second_input = deepcopy(completed_manifest()["inputs"]["episode-1"])
        mutations.append(
            lambda payload: payload["inputs"].update(
                {"episode-2": second_input}
            )
        )
        mutations.append(
            lambda payload: payload.update(
                {"completed_videos": ["different-media"]}
            )
        )
        mutations.append(
            lambda payload: payload["videos"]["episode-1"].update(
                {"state": "failed"}
            )
        )
        mutations.append(
            lambda payload: payload.update({"failed_videos": ["episode-1"]})
        )
        mutations.append(
            lambda payload: payload.update(
                {"interrupted_videos": ["episode-1"]}
            )
        )

        for mutate in mutations:
            payload = completed_manifest()
            mutate(payload)
            with self.assertRaises(ValidationError):
                self.validate(payload)

    def test_record_counts_exactly_match_modalities_and_sizes_are_nonnegative(self):
        invalid_counts = (
            {"scene": 4},
            {"scene": 4, "speech": 2, "actor": 1},
            {"scene": -1, "speech": 2},
        )
        for record_counts in invalid_counts:
            with self.subTest(record_counts=record_counts):
                payload = completed_manifest()
                payload["record_counts"] = record_counts
                with self.assertRaises(ValidationError):
                    self.validate(payload)

        unknown_size = completed_manifest()
        unknown_size["store_size_bytes_at_commit"] = None
        self.assertIsNone(
            self.validate(unknown_size).store_size_bytes_at_commit
        )

        for field in ("processed_frames", "store_size_bytes_at_commit"):
            with self.subTest(field=field):
                payload = completed_manifest()
                payload[field] = -1
                with self.assertRaises(ValidationError):
                    self.validate(payload)


if __name__ == "__main__":
    unittest.main()
