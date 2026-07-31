from __future__ import annotations

from vidxp.application_models import (
    CapabilityInfo,
    CapabilityOperationInfo,
    CapabilitySummary,
)
from vidxp.capabilities.registry import CapabilityRegistry


class CapabilityService:
    """Transport-neutral capability metadata projection."""

    def __init__(self, registry: CapabilityRegistry) -> None:
        self.registry = registry

    def list(self) -> tuple[CapabilitySummary, ...]:
        return tuple(self._summary(name) for name in self.registry.names())

    def get(self, name: str) -> CapabilityInfo:
        return self._info(name)

    def _summary(self, name: str) -> CapabilitySummary:
        definition = self.registry.get(name)
        return CapabilitySummary(
            name=definition.name,
            description=definition.description,
            install_extra=definition.extra,
            supports_indexing=definition.collection_name is not None,
            prepares_models=definition.prepares_models,
            roles=definition.roles,
            identity_mode=definition.identity_mode,
            provenance=self.registry.provenance(name),
        )

    def _info(self, name: str) -> CapabilityInfo:
        definition = self.registry.get(name)
        return CapabilityInfo(
            **self._summary(name).model_dump(),
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
        )
