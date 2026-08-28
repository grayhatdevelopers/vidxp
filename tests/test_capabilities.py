import unittest
from importlib.metadata import PackageNotFoundError
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pydantic import BaseModel, ValidationError

from vidxp.capabilities.actor.config import ActorConfig
from vidxp.capabilities.contracts import (
    CapabilityDefinition,
    CapabilityExecutor,
    CapabilityIndexResult,
    CapabilityInput,
    CapabilityOutput,
    CapabilityPlugin,
    CapabilityProvenance,
    OperationDefinition,
    RuntimeCheck,
    module_import_check,
)
from vidxp.capabilities.dialogue.config import DialogueConfig
from vidxp.capabilities.registry import (
    CapabilityRegistry,
    create_capability_registry,
)
from vidxp.capability_service import CapabilityService
from vidxp.capabilities.scene.config import SceneConfig
from vidxp.capabilities.sound.config import SoundConfig
from vidxp.capabilities.videoprism.config import VideoPrismConfig
from vidxp.core.contracts import IndexConfig
from vidxp.core.runner import _index_groups


class ExampleInput(CapabilityInput):
    value: int


class ExampleOutput(CapabilityOutput):
    doubled: int


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.registry = create_capability_registry()

    def test_module_import_checks_run_in_an_isolated_process(self):
        with patch(
            "vidxp.capabilities.contracts.subprocess.run",
            return_value=SimpleNamespace(returncode=0),
        ) as run:
            result = module_import_check(
                "OpenCV import",
                "cv2",
                "VideoCapture",
            ).inspect()

        self.assertTrue(result["ok"])
        command = run.call_args.args[0]
        self.assertEqual(command[1], "-c")
        self.assertIn('"cv2"', command[3])
        self.assertIn('"VideoCapture"', command[3])
        self.assertEqual(run.call_args.kwargs["timeout"], 180)

    def test_registry_drives_capability_metadata(self):
        self.assertEqual(
            self.registry.names(),
            ("dialogue", "sound", "scene", "actor", "videoprism"),
        )
        self.assertEqual(self.registry.index_names(), self.registry.names())
        self.assertEqual(
            self.registry.preparable_names(),
            ("dialogue", "sound", "scene", "actor", "videoprism"),
        )
        self.assertEqual(
            self.registry.collection_names(),
            {
                "dialogue": "dialogue",
                "sound": "sound",
                "scene": "scene",
                "actor": "actor",
                "videoprism": "videoprism",
            },
        )
        self.assertEqual(
            tuple(
                capability.label
                for capability in CapabilityService(self.registry).list()
            ),
            (
                "Dialogue search",
                "Sound event search",
                "Visual scene search",
                "Actor recognition",
                "Temporal video search",
            ),
        )

    def test_registered_operations_are_schema_only_metadata(self):
        self.assertNotIn("handler", OperationDefinition.model_fields)
        for definition in self.registry.definitions.values():
            self.assertIsInstance(definition, BaseModel)
            for operation in definition.operations.values():
                self.assertTrue(issubclass(operation.input_model, BaseModel))
                self.assertTrue(issubclass(operation.output_model, BaseModel))

    def test_internal_actor_cluster_lookup_is_not_advertised(self):
        operations = {
            operation.name
            for operation in CapabilityService(self.registry)
            .get("actor")
            .operations
        }

        self.assertNotIn("cluster", operations)
        self.assertIn("clusters", operations)
        self.assertIn("detections", operations)

    def test_capability_list_is_summary_only(self):
        service = CapabilityService(self.registry)

        summaries = service.list()
        detail = service.get("scene")

        self.assertEqual(
            tuple(summary.name for summary in summaries),
            self.registry.names(),
        )
        self.assertTrue(detail.operations)
        self.assertTrue(
            all(not hasattr(summary, "operations") for summary in summaries)
        )

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
        self.assertIs(self.registry.get("sound").config_model, SoundConfig)
        self.assertIs(
            self.registry.get("videoprism").config_model,
            VideoPrismConfig,
        )

        options = self.registry.validate_options(
            ("scene",),
            {"scene": {"batch_size": 4, "sample_fps": 0.5}},
        )
        self.assertEqual(options["scene"]["batch_size"], 4)
        self.assertEqual(options["scene"]["sample_fps"], 0.5)
        self.assertNotIn("model", options["scene"])
        with self.assertRaises(ValidationError):
            self.registry.validate_options(
                ("scene",),
                {"scene": {"sample_fps": 0}},
            )
        with self.assertRaises(ValidationError):
            self.registry.validate_options(
                ("scene",),
                {"scene": {"model": "unapproved/model"}},
            )
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
                ("dialogue", "sound", "scene", "actor", "videoprism"),
                self.registry,
            ),
            (
                ("dialogue",),
                ("sound",),
                ("scene", "actor", "videoprism"),
            ),
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
        self.assertIsNone(self.registry.executor("sound").index_processor)

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

    def test_external_plugins_require_exact_distribution_entry_point_pair(self):
        plugin = CapabilityPlugin(
            definition=CapabilityDefinition(
                name="ocr",
                description="OCR.",
                extra="ocr",
                operations={
                    "run": OperationDefinition(
                        input_model=ExampleInput,
                        output_model=ExampleOutput,
                        requires_index=False,
                    )
                },
            ),
            executor_factory=lambda: CapabilityExecutor(
                operations={
                    "run": lambda _context, request: {
                        "doubled": request.value * 2
                    }
                }
            ),
            requirements=("example-runtime>=1,<2",),
        )
        distribution = SimpleNamespace(
            name="acme-capabilities",
            version="1.2.3",
        )
        entry_point = SimpleNamespace(
            name="ocr",
            dist=distribution,
            load=Mock(return_value=plugin),
        )
        with patch(
            "vidxp.capabilities.registry.entry_points",
            return_value=(entry_point,),
        ):
            rejected = create_capability_registry(
                external=True,
                allowlist=("acme-capabilities:other",),
            )
            accepted = create_capability_registry(
                external=True,
                allowlist=("acme-capabilities:ocr",),
            )

        self.assertNotIn("ocr", rejected.names())
        self.assertIn("ocr", accepted.names())
        self.assertEqual(
            accepted.provenance("ocr"),
            CapabilityProvenance(
                distribution="acme-capabilities",
                entry_point="ocr",
                version="1.2.3",
            ),
        )
        self.assertEqual(
            [item.name for item in accepted.requirements_for(("ocr",))],
            ["example-runtime"],
        )
        with patch(
            "vidxp.dependencies.version",
            side_effect=PackageNotFoundError("example-runtime"),
        ):
            check = accepted.dependency_checks(("ocr",))[0]
        self.assertFalse(check.ok)
        self.assertEqual(check.capability, "ocr")
        self.assertEqual(check.provenance.distribution, "acme-capabilities")
        self.assertEqual(entry_point.load.call_count, 1)

    def test_plugin_contract_and_collision_errors_include_provenance(self):
        provenance = CapabilityProvenance(
            distribution="acme-capabilities",
            entry_point="ocr",
            version="1.2.3",
        )
        invalid = CapabilityPlugin(
            definition=CapabilityDefinition(
                name="ocr",
                description="OCR.",
                extra="ocr",
                operations={
                    "run": OperationDefinition(
                        input_model=ExampleInput,
                        output_model=ExampleOutput,
                        requires_index=False,
                    )
                },
            ),
            executor_factory=lambda: CapabilityExecutor(
                operations={"run": lambda _context, _request: {"doubled": 2}}
            ),
            contract_version=999,
            provenance=provenance,
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "acme-capabilities:ocr",
        ):
            CapabilityRegistry((invalid,))

        collision = invalid.model_copy(
            update={
                "definition": self.registry.get("scene"),
                "contract_version": 1,
            }
        )
        built_in = CapabilityPlugin(
            definition=self.registry.get("scene"),
            executor_factory=lambda: CapabilityExecutor(),
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "built-in VidXP capability.*acme-capabilities:ocr",
        ):
            CapabilityRegistry((built_in, collision))

    def test_external_plugin_loading_is_deterministic_and_errors_are_chained(self):
        def plugin(name):
            return CapabilityPlugin(
                definition=CapabilityDefinition(
                    name=name,
                    description=f"{name} capability.",
                    extra=name,
                    operations={
                        "run": OperationDefinition(
                            input_model=ExampleInput,
                            output_model=ExampleOutput,
                            requires_index=False,
                        )
                    },
                ),
                executor_factory=lambda: CapabilityExecutor(
                    operations={
                        "run": lambda _context, request: {
                            "doubled": request.value * 2
                        }
                    }
                ),
            )

        distribution = SimpleNamespace(name="acme", version="1")
        zeta = SimpleNamespace(
            name="zeta",
            dist=distribution,
            load=Mock(return_value=plugin("zeta")),
        )
        alpha = SimpleNamespace(
            name="alpha",
            dist=distribution,
            load=Mock(return_value=plugin("alpha")),
        )
        with patch(
            "vidxp.capabilities.registry.entry_points",
            return_value=(zeta, alpha),
        ):
            registry = create_capability_registry(
                external=True,
                allowlist=("acme:zeta", "acme:alpha"),
            )
        self.assertEqual(registry.names()[-2:], ("alpha", "zeta"))

        failure = RuntimeError("factory failure")
        broken = SimpleNamespace(
            name="broken",
            dist=distribution,
            load=Mock(side_effect=failure),
        )
        with (
            patch(
                "vidxp.capabilities.registry.entry_points",
                return_value=(broken,),
            ),
            self.assertRaisesRegex(RuntimeError, "acme:broken") as raised,
        ):
            create_capability_registry(
                external=True,
                allowlist=("acme:broken",),
            )
        self.assertIs(raised.exception.__cause__, failure)

    def test_external_runtime_check_errors_are_sanitized_with_provenance(self):
        events = []

        def fail_runtime_check():
            events.append("inspect")
            raise RuntimeError("token=do-not-leak")

        provenance = CapabilityProvenance(
            distribution="acme-capabilities",
            entry_point="ocr",
            version="1.2.3",
        )
        plugin = CapabilityPlugin(
            definition=CapabilityDefinition(
                name="ocr",
                description="OCR.",
                extra="ocr",
                operations={
                    "run": OperationDefinition(
                        input_model=ExampleInput,
                        output_model=ExampleOutput,
                        requires_index=False,
                    )
                },
            ),
            executor_factory=lambda: CapabilityExecutor(
                operations={
                    "run": lambda _context, request: {
                        "doubled": request.value * 2
                    }
                },
                runtime_checks=(
                    RuntimeCheck(
                        label="ocr runtime",
                        check=fail_runtime_check,
                    ),
                ),
            ),
            provenance=provenance,
        )

        check = CapabilityRegistry((plugin,)).dependency_checks(
            ("ocr",),
            on_check_start=lambda capability, _kind, name: events.append(
                (capability, name)
            ),
        )[0]

        self.assertEqual(events, [("ocr", "ocr runtime"), "inspect"])
        self.assertEqual(check.error, "runtime check failed")
        self.assertNotIn("do-not-leak", check.model_dump_json())
        self.assertEqual(check.provenance, provenance)


if __name__ == "__main__":
    unittest.main()
