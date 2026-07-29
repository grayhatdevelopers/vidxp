import json
import unittest
from datetime import datetime, timezone
from uuid import uuid1, uuid4

from pydantic import ValidationError

from vidxp.core.snapshots import (
    ACTIVE_SNAPSHOT_POINTER_SCHEMA_VERSION,
    INDEX_SNAPSHOT_SCHEMA_VERSION,
    ActiveSnapshotPointer,
    GenerationReference,
    IndexSnapshot,
)


SHA256 = "a" * 64
OTHER_SHA256 = "b" * 64


class SnapshotModelTests(unittest.TestCase):
    def generation_reference(self, **changes):
        values = {
            "generation_id": uuid4().hex,
            "media_id": "media-1",
            "manifest_sha256": SHA256,
            "input_sha256": OTHER_SHA256,
            "config_fingerprint": SHA256,
            "modalities": ("dialogue", "scene"),
            "record_counts": {"dialogue": 3, "scene": 4},
            "store_size_bytes_at_commit": 2048,
        }
        values.update(changes)
        return GenerationReference(**values)

    def index_snapshot(self, **changes):
        reference = changes.pop("reference", self.generation_reference())
        values = {
            "snapshot_id": uuid4().hex,
            "created_at": datetime(2026, 7, 29, tzinfo=timezone.utc),
            "config_fingerprint": SHA256,
            "configuration": {
                "device": "cpu",
                "modalities": ["dialogue", "scene"],
                "options": {"batch_size": 8},
            },
            "generations": {reference.media_id: reference},
        }
        values.update(changes)
        return IndexSnapshot(**values)

    def test_models_round_trip_through_json(self):
        snapshot = self.index_snapshot()
        pointer = ActiveSnapshotPointer(
            snapshot_id=snapshot.snapshot_id,
            snapshot_sha256=OTHER_SHA256,
            updated_at=datetime(2026, 7, 29, 1, 2, tzinfo=timezone.utc),
        )

        reference = next(iter(snapshot.generations.values()))
        self.assertEqual(
            GenerationReference.model_validate_json(
                reference.model_dump_json()
            ),
            reference,
        )
        self.assertEqual(
            IndexSnapshot.model_validate_json(snapshot.model_dump_json()),
            snapshot,
        )
        self.assertEqual(
            ActiveSnapshotPointer.model_validate_json(
                pointer.model_dump_json()
            ),
            pointer,
        )
        self.assertEqual(
            json.loads(snapshot.model_dump_json())["schema_version"],
            INDEX_SNAPSHOT_SCHEMA_VERSION,
        )
        self.assertEqual(
            json.loads(pointer.model_dump_json())["schema_version"],
            ACTIVE_SNAPSHOT_POINTER_SCHEMA_VERSION,
        )

    def test_models_are_strict_frozen_and_forbid_extra_fields(self):
        reference = self.generation_reference()
        with self.assertRaises(ValidationError):
            reference.store_size_bytes_at_commit = 1
        with self.assertRaises(ValidationError):
            self.generation_reference(store_size_bytes_at_commit="2048")
        with self.assertRaises(ValidationError):
            GenerationReference(
                **reference.model_dump(),
                unexpected=True,
            )

    def test_ids_must_be_lowercase_uuid4_hex(self):
        invalid_ids = (
            str(uuid4()),
            uuid4().hex.upper(),
            uuid1().hex,
            "0" * 32,
        )
        for invalid_id in invalid_ids:
            with self.subTest(invalid_id=invalid_id):
                with self.assertRaises(ValidationError):
                    self.generation_reference(generation_id=invalid_id)
                with self.assertRaises(ValidationError):
                    self.index_snapshot(snapshot_id=invalid_id)

    def test_sha256_fields_require_lowercase_64_character_hex(self):
        for invalid_hash in ("a" * 63, "A" * 64, "g" * 64):
            with self.subTest(invalid_hash=invalid_hash):
                with self.assertRaises(ValidationError):
                    self.generation_reference(
                        manifest_sha256=invalid_hash
                    )
                with self.assertRaises(ValidationError):
                    ActiveSnapshotPointer(
                        snapshot_id=uuid4().hex,
                        snapshot_sha256=invalid_hash,
                        updated_at=datetime.now(timezone.utc),
                    )

    def test_snapshot_rejects_mismatched_media_mapping(self):
        reference = self.generation_reference(media_id="media-1")
        with self.assertRaisesRegex(
            ValidationError,
            "mapping keys must match",
        ):
            self.index_snapshot(
                reference=reference,
                generations={"media-2": reference},
            )

    def test_generation_counts_must_match_modalities(self):
        with self.assertRaisesRegex(ValidationError, "exactly match"):
            self.generation_reference(record_counts={"scene": 1})
        with self.assertRaises(ValidationError):
            self.generation_reference(
                record_counts={"dialogue": -1, "scene": 1}
            )

    def test_generation_store_size_may_be_unknown(self):
        reference = self.generation_reference(
            store_size_bytes_at_commit=None
        )

        self.assertIsNone(reference.store_size_bytes_at_commit)

    def test_snapshot_configuration_must_be_json_safe(self):
        with self.assertRaises(ValidationError):
            self.index_snapshot(configuration={"runtime": object()})

    def test_timestamps_must_be_timezone_aware(self):
        with self.assertRaises(ValidationError):
            self.index_snapshot(created_at=datetime(2026, 7, 29))
        with self.assertRaises(ValidationError):
            ActiveSnapshotPointer(
                snapshot_id=uuid4().hex,
                snapshot_sha256=SHA256,
                updated_at=datetime(2026, 7, 29),
            )


if __name__ == "__main__":
    unittest.main()
