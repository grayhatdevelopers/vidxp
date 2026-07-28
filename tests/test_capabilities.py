import unittest

from pydantic import BaseModel, ValidationError

from vidxp.capabilities.actor.config import ActorConfig
from vidxp.capabilities.contracts import (
    CapabilityDefinition,
    CapabilityExecutor,
    CapabilityIndexResult,
    CapabilityInput,
    CapabilityOutput,
    CapabilityPlugin,
    OperationDefinition,
)
from vidxp.capabilities.dialogue.config import DialogueConfig
from vidxp.capabilities.registry import (
    CapabilityRegistry,
    create_capability_registry,
)
from vidxp.capabilities.scene.config import SceneConfig
from vidxp.core.contracts import IndexConfig
from vidxp.core.runner import _index_groups


class ExampleInput(CapabilityInput):
    value: int


class ExampleOutput(CapabilityOutput):
    doubled: int


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.registry = create_capability_registry()

    def test_registry_drives_capability_metadata(self):
        self.assertEqual(
            self.registry.names(),
            ("dialogue", "scene", "actor"),
        )
        self.assertEqual(self.registry.index_names(), self.registry.names())
        self.assertEqual(
            self.registry.preparable_names(),
            ("dialogue", "scene", "actor"),
        )
        self.assertEqual(
            self.registry.collection_names(),
            {
                "dialogue": "dialogue",
                "scene": "scene",
                "actor": "actor",
            },
        )

    def test_registered_operations_are_schema_only_metadata(self):
        self.assertNotIn("handler", OperationDefinition.model_fields)
        for definition in self.registry.definitions.values():
            self.assertIsInstance(definition, BaseModel)
            for operation in definition.operations.values():
                self.assertTrue(issubclass(operation.input_model, BaseModel))
                self.assertTrue(issubclass(operation.output_model, BaseModel))

    def test_executor_handlers_match_declared_operations(self):
        for name, definition in self.registry.definitions.items():
            self.assertEqual(
                set(self.registry.executor(name).operations),
                set(definition.operations),
            )

    def test_contracts_are_frozen_and_index_metadata_is_complete(self):
        with self.assertRaises(ValidationError):
            self.registry.get("scene").name = "changed"
        with self.assertRaises(ValidationError):
            CapabilityDefinition(
                name="broken",
                description="Incomplete index integration.",
                extra="broken",
                collection_name="broken",
            )
        with self.assertRaises(ValidationError):
            CapabilityIndexResult(summary={}, timings={"index": -1})

    def test_built_in_settings_are_owned_and_validated(self):
        self.assertIs(
            self.registry.get("dialogue").config_model,
            DialogueConfig,
        )
        self.assertIs(self.registry.get("scene").config_model, SceneConfig)
        self.assertIs(self.registry.get("actor").config_model, ActorConfig)

        options = self.registry.validate_options(
            ("scene",),
            {"scene": {"batch_size": 4, "model": "test-model"}},
        )
        self.assertEqual(options["scene"]["batch_size"], 4)
        self.assertEqual(options["scene"]["model"], "test-model")
        with self.assertRaises(ValidationError):
            self.registry.validate_options(
                ("actor",),
                {"actor": {"match_threshold": 2}},
            )

    def test_registry_rejects_executor_metadata_drift(self):
        definition = CapabilityDefinition(
            name="export",
            description="Export.",
            extra="export",
            operations={
                "run": OperationDefinition(
                    input_model=ExampleInput,
                    output_model=ExampleOutput,
                    requires_index=False,
                )
            },
        )
        registry = CapabilityRegistry(
            (
                CapabilityPlugin(
                    definition=definition,
                    executor_factory=lambda: CapabilityExecutor(),
                ),
            )
        )
        with self.assertRaisesRegex(RuntimeError, "operation handlers"):
            registry.executor("export")

    def test_operation_only_capability_needs_no_index_metadata(self):
        capability = CapabilityDefinition(
            name="export",
            description="Export results.",
            extra="export",
            operations={
                "run": OperationDefinition(
                    input_model=ExampleInput,
                    output_model=ExampleOutput,
                    requires_index=False,
                )
            },
        )
        self.assertIsNone(capability.collection_name)
        self.assertIsNone(capability.execution_group)

    def test_visual_execution_group_is_explicit(self):
        self.assertEqual(
            _index_groups(
                ("dialogue", "scene", "actor"),
                self.registry,
            ),
            (("dialogue",), ("scene", "actor")),
        )
        self.assertIsNotNone(
            self.registry.executor("scene").index_processor
        )
        self.assertIsNotNone(
            self.registry.executor("actor").index_processor
        )
        self.assertIsNone(
            self.registry.executor("dialogue").index_processor
        )

    def test_core_config_has_no_provider_specific_fields(self):
        fields = IndexConfig.__dataclass_fields__
        for name in (
            "sentence_model",
            "whisper_model",
            "clip_model",
            "actor_batch_size",
            "face_match_threshold",
        ):
            self.assertNotIn(name, fields)


if __name__ == "__main__":
    unittest.main()
