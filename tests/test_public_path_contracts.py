import json
import unittest
from dataclasses import is_dataclass
from pathlib import Path

from pydantic import BaseModel, ValidationError

from vidxp.application_models import (
    Artifact,
    CreateActorOverlayCommand,
    CreateIndexCommand,
    CreateSnippetCommand,
    ImportMediaCommand,
    IndexResult,
    IndexStatus,
    MediaAsset,
    MediaPage,
    PrepareModelsResult,
)
from vidxp.capabilities.registry import create_capability_registry
from vidxp.capabilities.contracts import OperationDefinition
from vidxp.capabilities.schemas import SearchHit
from vidxp.ports import LocalFileResource


MEDIA_ID = "123456781234423481234567890abcde"
GENERATION_ID = "223456781234423481234567890abcde"


def schema_fields(schema):
    fields = set()
    if isinstance(schema, dict):
        fields.update(schema.get("properties", ()))
        for value in schema.values():
            fields.update(schema_fields(value))
    elif isinstance(schema, list):
        for value in schema:
            fields.update(schema_fields(value))
    return fields


class PublicPathContractTests(unittest.TestCase):
    def test_shared_contracts_do_not_expose_paths_or_storage_keys(self):
        models = [
            Artifact,
            CreateActorOverlayCommand,
            CreateIndexCommand,
            CreateSnippetCommand,
            IndexResult,
            IndexStatus,
            MediaAsset,
            MediaPage,
            PrepareModelsResult,
            SearchHit,
        ]
        registry = create_capability_registry()
        for definition in registry.definitions.values():
            for operation in definition.operations.values():
                models.extend((operation.input_model, operation.output_model))

        forbidden = {
            "path",
            "input_path",
            "output_path",
            "storage_key",
            "repository_root",
            "index_directory",
        }
        for model in models:
            with self.subTest(model=model.__name__):
                schema = model.model_json_schema()
                self.assertTrue(forbidden.isdisjoint(schema_fields(schema)))
                self.assertNotIn('"format": "path"', json.dumps(schema))

    def test_only_local_import_accepts_a_path(self):
        command = ImportMediaCommand(path=Path("video.mp4"))
        self.assertEqual(command.path, Path("video.mp4"))
        with self.assertRaises(ValidationError):
            CreateIndexCommand.model_validate(
                {
                    "path": "video.mp4",
                    "media_id": MEDIA_ID,
                    "modalities": ["scene"],
                }
            )

    def test_media_and_artifact_ids_are_not_content_hashes_or_names(self):
        with self.assertRaises(ValidationError):
            CreateIndexCommand(media_id="episode-1", modalities=("scene",))
        with self.assertRaises(ValidationError):
            CreateIndexCommand(media_id="1" * 64, modalities=("scene",))

    def test_scene_sampling_requires_a_positive_scene_request(self):
        with self.assertRaises(ValidationError):
            CreateIndexCommand(
                media_id=MEDIA_ID,
                modalities=("dialogue",),
                scene_sample_fps=1.0,
            )
        with self.assertRaises(ValidationError):
            CreateIndexCommand(
                media_id=MEDIA_ID,
                modalities=("scene",),
                scene_sample_fps=0,
            )

    def test_scene_sampling_has_one_canonical_command_field(self):
        nested = CreateIndexCommand(
            media_id=MEDIA_ID,
            modalities=("scene",),
            capability_options={
                "scene": {"sample_fps": 2.0, "batch_size": 4},
            },
        )
        explicit = CreateIndexCommand(
            media_id=MEDIA_ID,
            modalities=("scene",),
            scene_sample_fps=2.0,
            capability_options={"scene": {"batch_size": 4}},
        )

        self.assertEqual(nested, explicit)
        self.assertEqual(nested.scene_sample_fps, 2.0)
        self.assertEqual(
            nested.capability_options,
            {"scene": {"batch_size": 4}},
        )
        self.assertNotIn(
            "sample_fps",
            nested.model_dump(mode="json")["capability_options"]["scene"],
        )
        with self.assertRaisesRegex(ValidationError, "conflicts"):
            CreateIndexCommand(
                media_id=MEDIA_ID,
                modalities=("scene",),
                scene_sample_fps=1.0,
                capability_options={"scene": {"sample_fps": 2.0}},
            )

    def test_search_metadata_rejects_internal_locations(self):
        with self.assertRaises(ValidationError):
            SearchHit(
                rank=1,
                media_id=MEDIA_ID,
                video_id=MEDIA_ID,
                generation_id=GENERATION_ID,
                start=1,
                end=2,
                score=0,
                raw_distance=0,
                modality="scene",
                source_id="source",
                metadata={"source_path": "C:/secret/video.mp4"},
            )

        with self.assertRaises(ValidationError):
            SearchHit(
                rank=1,
                media_id=MEDIA_ID,
                video_id=MEDIA_ID,
                generation_id=GENERATION_ID,
                start=1,
                end=2,
                score=0,
                raw_distance=0,
                modality="scene",
                source_id="source",
                metadata={"source": {"storage_key": "secret"}},
            )

    def test_external_public_operation_cannot_expose_paths(self):
        class UnsafeInput(BaseModel):
            path: Path

        class SafeOutput(BaseModel):
            ok: bool

        with self.assertRaises(ValidationError):
            OperationDefinition(
                input_model=UnsafeInput,
                output_model=SafeOutput,
            )

    def test_local_file_handle_is_not_dataclass_serializable(self):
        handle = LocalFileResource(
            path=Path("internal"),
            filename="video.mp4",
            mime_type="video/mp4",
            byte_size=1,
            etag="1" * 64,
        )
        self.assertFalse(is_dataclass(handle))
        self.assertFalse(hasattr(handle, "__dict__"))


if __name__ == "__main__":
    unittest.main()
