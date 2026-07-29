from __future__ import annotations

from vidxp.application_models import (
    CapabilityInfo,
    CapabilityOperationInfo,
)
from vidxp.capabilities.registry import CapabilityRegistry


class CapabilityService:
    """Transport-neutral capability metadata projection."""

    def __init__(self, registry: CapabilityRegistry) -> None:
        self.registry = registry

    def list(self) -> tuple[CapabilityInfo, ...]:
        return tuple(self._info(name) for name in self.registry.names())

    def get(self, name: str) -> CapabilityInfo:
        return self._info(name)

    def _info(self, name: str) -> CapabilityInfo:
        definition = self.registry.get(name)
        return CapabilityInfo(
            name=definition.name,
            description=definition.description,
            install_extra=definition.extra,
            supports_indexing=definition.collection_name is not None,
            prepares_models=definition.prepares_models,
            operations=tuple(
                CapabilityOperationInfo(
                    name=operation_name,
                    requires_index=operation.requires_index,
                    input_schema=operation.input_model.model_json_schema(),
                    output_schema=operation.output_model.model_json_schema(),
                )
                for operation_name, operation in definition.operations.items()
                if operation.public
            ),
            provenance=self.registry.provenance(name),
        )
