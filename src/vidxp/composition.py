from __future__ import annotations

from vidxp.application import VidXPApplication
from vidxp.capabilities.registry import create_capability_registry
from vidxp.infrastructure.local_index import LocalIndexBackend
from vidxp.runtime import ModelRuntime
from vidxp.settings import VidXPSettings


def create_application(
    settings: VidXPSettings | None = None,
) -> VidXPApplication:
    active_settings = settings or VidXPSettings()
    registry = create_capability_registry(
        external=active_settings.external_capabilities,
        allowlist=active_settings.capability_allowlist,
    )
    runtime = ModelRuntime(active_settings)
    backend = LocalIndexBackend(registry, runtime)
    return VidXPApplication(
        layout=active_settings.layout,
        registry=registry,
        runtime=runtime,
        index_backend=backend,
    )
