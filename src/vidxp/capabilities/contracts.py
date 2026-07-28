from __future__ import annotations

from importlib import import_module
from types import MappingProxyType
from typing import Any, Callable, Mapping

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeFloat,
    field_validator,
    model_validator,
)
from packaging.requirements import Requirement

from vidxp.core.contracts import IndexConfig, VideoSource
from vidxp.core.indexing_common import ProgressCallback
from vidxp.model_contracts import ArtifactSpec, ModelSpec
from vidxp.ports import IndexReader, ModelRuntimePort
from vidxp.application_models import CapabilityProvenance


CAPABILITY_CONTRACT_VERSION = 1


class _ContractModel(BaseModel):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
    )


class CapabilityInput(BaseModel):
    """Base model for validated capability input."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class CapabilityOutput(_ContractModel):
    """Base model for validated capability output."""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
    )


class CapabilityConfig(_ContractModel):
    """Base model for settings owned and validated by one capability."""


class RuntimeCheck(_ContractModel):
    """One non-package environment requirement."""

    label: str = Field(min_length=1)
    check: Callable[[], str | None]
    applies_to: Callable[[VideoSource], bool] | None = None

    def inspect(self) -> dict[str, Any]:
        try:
            detail = self.check()
        except Exception:
            return {
                "name": self.label,
                "ok": False,
                "error": "runtime check failed",
            }
        result: dict[str, Any] = {
            "name": self.label,
            "ok": True,
            "error": None,
        }
        if detail is not None:
            result["path"] = detail
        return result

    def applies(self, source: VideoSource | None) -> bool:
        return (
            source is None
            or self.applies_to is None
            or self.applies_to(source)
        )


class RuntimeCheckBinding(_ContractModel):
    """Runtime check owned by a capability or platform service."""

    capability: str = Field(min_length=1)
    check: RuntimeCheck
    provenance: CapabilityProvenance | None = None


class CapabilityContext(_ContractModel):
    """Runtime context shared by transport-neutral capability operations."""

    config: IndexConfig | None
    runtime: ModelRuntimePort
    storage: IndexReader | None = None

    def require_config(self) -> IndexConfig:
        if self.config is None:
            raise RuntimeError("This operation requires an active index.")
        return self.config

    def require_storage(self) -> IndexReader:
        if self.storage is None:
            raise RuntimeError("This operation requires an active index store.")
        return self.storage


class PreparationContext(_ContractModel):
    """Runtime values supplied to one capability's preparation hook."""

    runtime: ModelRuntimePort
    settings: CapabilityConfig


OperationHandler = Callable[[CapabilityContext, BaseModel], BaseModel | Mapping]


class OperationDefinition(_ContractModel):
    """Transport-neutral input and output metadata for one operation."""

    input_model: type[BaseModel]
    output_model: type[BaseModel]
    requires_index: bool = True
    public: bool = True

    @field_validator("input_model", "output_model")
    @classmethod
    def _require_model(
        cls,
        value: type[BaseModel],
    ) -> type[BaseModel]:
        if not isinstance(value, type) or not issubclass(value, BaseModel):
            raise ValueError("Operation schemas must be Pydantic models.")
        return value

    @model_validator(mode="after")
    def _validate_public_schemas(self) -> "OperationDefinition":
        if not self.public:
            return self
        forbidden_names = {
            "path",
            "input_path",
            "output_path",
            "storage_key",
            "repository_root",
            "index_directory",
        }

        def inspect(value: Any) -> None:
            if isinstance(value, dict):
                properties = value.get("properties")
                if isinstance(properties, dict):
                    unsafe = {
                        name
                        for name in properties
                        if (
                            name in forbidden_names
                            or name.endswith("_path")
                            or name.endswith("_directory")
                        )
                    }
                    if unsafe:
                        fields = ", ".join(sorted(unsafe))
                        raise ValueError(
                            f"Public operation schema exposes {fields}."
                        )
                if value.get("format") in {"path", "binary"}:
                    raise ValueError(
                        "Public operation schema exposes local or binary data."
                    )
                if value.get("contentEncoding") == "base64":
                    raise ValueError(
                        "Public operation schema embeds binary content."
                    )
                for nested in value.values():
                    inspect(nested)
            elif isinstance(value, list):
                for nested in value:
                    inspect(nested)

        inspect(self.input_model.model_json_schema())
        inspect(self.output_model.model_json_schema())
        return self


class CapabilityIndexResult(_ContractModel):
    """Summary and timing data returned by an indexing handler."""

    summary: dict[str, Any]
    timings: dict[str, NonNegativeFloat] = Field(default_factory=dict)


IndexHandler = Callable[..., CapabilityIndexResult]
PrepareHandler = Callable[
    [PreparationContext, ProgressCallback | None],
    tuple[str, ...],
]
RequirementFilter = Callable[
    [VideoSource, tuple[Requirement, ...]],
    tuple[Requirement, ...],
]
ModelManifest = Callable[
    [IndexConfig, tuple[VideoSource, ...]],
    Mapping[str, Any],
]
ExecutorFactory = Callable[[], "CapabilityExecutor"]


class CapabilityDefinition(_ContractModel):
    """Domain metadata for one named capability."""

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    extra: str = Field(min_length=1)
    config_model: type[CapabilityConfig] = CapabilityConfig
    collection_name: str | None = None
    index_stage: str | None = None
    execution_group: str | None = None
    operations: Mapping[str, OperationDefinition] = Field(default_factory=dict)
    model_specs: tuple[ModelSpec | ArtifactSpec, ...] = ()
    prepares_models: bool = False

    @field_validator("config_model")
    @classmethod
    def _require_config_model(
        cls,
        value: type[CapabilityConfig],
    ) -> type[CapabilityConfig]:
        if (
            not isinstance(value, type)
            or not issubclass(value, CapabilityConfig)
        ):
            raise ValueError(
                "Capability config_model must extend CapabilityConfig."
            )
        return value

    @field_validator("operations")
    @classmethod
    def _freeze_operations(
        cls,
        value: Mapping[str, OperationDefinition],
    ) -> Mapping[str, OperationDefinition]:
        return MappingProxyType(dict(value))

    @model_validator(mode="after")
    def _require_complete_metadata(self) -> CapabilityDefinition:
        indexing_fields = (
            self.collection_name,
            self.index_stage,
            self.execution_group,
        )
        if any(value is not None for value in indexing_fields) and not all(
            value is not None for value in indexing_fields
        ):
            raise ValueError(
                "Indexable capabilities must declare collection names, "
                "an index stage, and an execution group together."
            )
        if self.collection_name is None and not self.operations:
            raise ValueError(
                "A capability must support indexing or at least one operation."
            )
        return self


def module_import_check(
    label: str,
    module_name: str,
    *attributes: str,
) -> RuntimeCheck:
    def check() -> None:
        module = import_module(module_name)
        for attribute in attributes:
            if not hasattr(module, attribute):
                raise AttributeError(
                    f"{module_name} does not expose {attribute}."
                )

    return RuntimeCheck(label=label, check=check)


class CapabilityExecutor(_ContractModel):
    """Infrastructure hooks bound to one capability definition."""

    indexer: IndexHandler | None = None
    index_processor: Any | None = None
    operations: Mapping[str, OperationHandler] = Field(default_factory=dict)
    runtime_checks: tuple[RuntimeCheck, ...] = ()
    requirement_filter: RequirementFilter | None = None
    prepare: PrepareHandler | None = None
    model_manifest: ModelManifest | None = None

    @field_validator("operations")
    @classmethod
    def _freeze_operation_handlers(
        cls,
        value: Mapping[str, OperationHandler],
    ) -> Mapping[str, OperationHandler]:
        return MappingProxyType(dict(value))

    def source_requirements(
        self,
        source: VideoSource,
        requirements: tuple[Requirement, ...],
    ) -> tuple[Requirement, ...]:
        if self.requirement_filter is None:
            return requirements
        return self.requirement_filter(source, requirements)


class CapabilityPlugin(_ContractModel):
    definition: CapabilityDefinition
    executor_factory: ExecutorFactory
    contract_version: int = CAPABILITY_CONTRACT_VERSION
    requirements: tuple[str, ...] = ()
    provenance: CapabilityProvenance | None = None

    @field_validator("requirements")
    @classmethod
    def _validate_requirements(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        for value in values:
            Requirement(value)
        return values


def capability_install_hint(name: str) -> str:
    return f'Install the capability with: pip install "vidxp[{name}]"'


class CapabilityRequestError(ValueError):
    """Expected invalid capability selection or options."""


class CapabilityDependencyError(RuntimeError):
    def __init__(
        self,
        capabilities: tuple[str, ...],
        failures: tuple[Mapping[str, Any], ...],
    ) -> None:
        self.capabilities = capabilities
        self.failures = failures
        super().__init__("Capability dependencies are unavailable.")
