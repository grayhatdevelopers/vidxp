from __future__ import annotations

from importlib.metadata import EntryPoint, entry_points
from threading import RLock
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from vidxp.capabilities.contracts import (
    CAPABILITY_CONTRACT_VERSION,
    CapabilityDefinition,
    CapabilityExecutor,
    CapabilityPlugin,
    RuntimeCheck,
    capability_install_hint,
)
from vidxp.core.contracts import VideoSource
from vidxp.dependencies import (
    active_requirements,
    inspect_requirement,
    installed_base_requirements,
    packaged_requirements,
)


ENTRY_POINT_GROUP = "vidxp.capabilities"


class CapabilityRegistry:
    """Validated capability metadata with lazily constructed executors."""

    def __init__(self, plugins: Iterable[CapabilityPlugin]) -> None:
        indexed: dict[str, CapabilityPlugin] = {}
        for plugin in plugins:
            if plugin.contract_version != CAPABILITY_CONTRACT_VERSION:
                raise RuntimeError(
                    f"Capability {plugin.definition.name!r} targets contract "
                    f"{plugin.contract_version}; VidXP requires "
                    f"{CAPABILITY_CONTRACT_VERSION}."
                )
            name = plugin.definition.name
            if name in indexed:
                raise RuntimeError(f"Duplicate capability name: {name!r}.")
            indexed[name] = plugin
        self._plugins = MappingProxyType(indexed)
        self._executors: dict[str, CapabilityExecutor] = {}
        self._executor_lock = RLock()

    @property
    def definitions(self) -> Mapping[str, CapabilityDefinition]:
        return MappingProxyType(
            {name: plugin.definition for name, plugin in self._plugins.items()}
        )

    def names(self) -> tuple[str, ...]:
        return tuple(self._plugins)

    def index_names(self) -> tuple[str, ...]:
        return tuple(
            name
            for name, definition in self.definitions.items()
            if definition.collection_name is not None
        )

    def preparable_names(self) -> tuple[str, ...]:
        return tuple(
            name
            for name, definition in self.definitions.items()
            if definition.prepares_models
        )

    def get(self, name: str) -> CapabilityDefinition:
        try:
            return self._plugins[name].definition
        except KeyError as exc:
            available = ", ".join(self.names())
            raise ValueError(
                f"Unknown capability {name!r}. "
                f"Available capabilities: {available}."
            ) from exc

    def executor(self, name: str) -> CapabilityExecutor:
        definition = self.get(name)
        with self._executor_lock:
            if name not in self._executors:
                executor = self._plugins[name].executor_factory()
                operation_names = set(definition.operations)
                if set(executor.operations) != operation_names:
                    raise RuntimeError(
                        f"Capability {name!r} operation handlers do not match "
                        "its declared operations."
                    )
                if (executor.indexer is not None) != (
                    definition.collection_name is not None
                ):
                    raise RuntimeError(
                        f"Capability {name!r} indexing metadata and executor "
                        "do not match."
                    )
                if (executor.prepare is not None) != definition.prepares_models:
                    raise RuntimeError(
                        f"Capability {name!r} preparation metadata and executor "
                        "do not match."
                    )
                self._executors[name] = executor
        return self._executors[name]

    def validate_names(self, names: Iterable[str]) -> tuple[str, ...]:
        selected = tuple(dict.fromkeys(str(name).strip() for name in names))
        if not selected:
            raise ValueError("At least one capability is required.")
        for name in selected:
            self.get(name)
        return selected

    def collection_names(
        self,
        names: Iterable[str] | None = None,
    ) -> dict[str, str]:
        selected = self.index_names() if names is None else self.validate_names(names)
        return {
            name: collection_name
            for name in selected
            if (collection_name := self.get(name).collection_name) is not None
        }

    def validate_options(
        self,
        names: Iterable[str],
        options: Mapping[str, Mapping[str, Any]] | None,
    ) -> dict[str, dict[str, Any]]:
        selected = self.validate_names(names)
        supplied = dict(options or {})
        unknown = sorted(set(supplied) - set(selected))
        if unknown:
            raise ValueError(
                "Options were supplied for disabled capabilities: "
                + ", ".join(unknown)
            )
        return {
            name: self.get(name)
            .config_model.model_validate(supplied.get(name, {}))
            .model_dump(mode="python")
            for name in selected
        }

    def requirements_for(
        self,
        names: Iterable[str],
        *,
        source: VideoSource | None = None,
    ) -> tuple[Requirement, ...]:
        selected = self.validate_names(names)
        requirements = list(
            active_requirements(
                packaged_requirements("vidxp", "requirements/storage.txt")
            )
            if any(self.get(name).collection_name for name in selected)
            else ()
        )
        for name in selected:
            capability_requirements = active_requirements(
                packaged_requirements(f"vidxp.capabilities.{name}")
            )
            executor = self.executor(name)
            requirements.extend(
                capability_requirements
                if source is None
                else executor.source_requirements(
                    source,
                    capability_requirements,
                )
            )
        unique = {str(requirement): requirement for requirement in requirements}
        return tuple(unique.values())

    def runtime_checks_for(
        self,
        names: Iterable[str],
        *,
        source: VideoSource | None = None,
    ) -> tuple[RuntimeCheck, ...]:
        checks = (
            check
            for name in self.validate_names(names)
            for check in self.executor(name).runtime_checks
            if check.applies(source)
        )
        return tuple({check.label: check for check in checks}.values())

    def dependency_checks(
        self,
        names: Iterable[str],
    ) -> tuple[dict[str, Any], ...]:
        return tuple(
            inspect_requirement(requirement)
            for requirement in self.requirements_for(names)
        ) + tuple(
            check.inspect()
            for check in self.runtime_checks_for(names)
        )

    def require_dependencies(
        self,
        names: Iterable[str],
        *,
        source: VideoSource,
    ) -> None:
        selected = self.validate_names(names)
        failures = [
            result
            for requirement in self.requirements_for(selected, source=source)
            if not (result := inspect_requirement(requirement))["ok"]
        ]
        failures.extend(
            result
            for check in self.runtime_checks_for(selected, source=source)
            if not (result := check.inspect())["ok"]
        )
        if failures:
            details = "; ".join(
                f"{failure['name']}: {failure['error']}"
                for failure in failures
            )
            extras = ",".join(self.get(name).extra for name in selected)
            raise RuntimeError(
                f"Capability dependencies are unavailable: {details}. "
                + capability_install_hint(extras)
            )

    def runtime_distributions(self) -> tuple[str, ...]:
        distributions = {
            canonicalize_name(requirement.name)
            for requirement in installed_base_requirements()
        }
        distributions.update(
            canonicalize_name(requirement.name)
            for requirement in self.requirements_for(self.names())
        )
        return tuple(sorted(distributions, key=str.lower))


def _builtin_plugins() -> tuple[CapabilityPlugin, ...]:
    from vidxp.capabilities.actor.definition import PLUGIN as actor
    from vidxp.capabilities.dialogue.definition import PLUGIN as dialogue
    from vidxp.capabilities.scene.definition import PLUGIN as scene

    return dialogue, scene, actor


def _external_entry_points(allowlist: tuple[str, ...]) -> tuple[EntryPoint, ...]:
    allowed = {canonicalize_name(value) for value in allowlist}
    candidates = entry_points(group=ENTRY_POINT_GROUP)
    return tuple(
        sorted(
            (
                entry_point
                for entry_point in candidates
                if canonicalize_name(entry_point.name) in allowed
                or (
                    entry_point.dist is not None
                    and canonicalize_name(entry_point.dist.name) in allowed
                )
            ),
            key=lambda item: (
                canonicalize_name(
                    item.dist.name if item.dist is not None else ""
                ),
                canonicalize_name(item.name),
            ),
        )
    )


def create_capability_registry(
    *,
    external: bool = False,
    allowlist: tuple[str, ...] = (),
) -> CapabilityRegistry:
    plugins = list(_builtin_plugins())
    if external:
        for entry_point in _external_entry_points(allowlist):
            try:
                loaded = entry_point.load()
                plugin = (
                loaded()
                if callable(loaded)
                and not isinstance(loaded, CapabilityPlugin)
                else loaded
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Could not load capability entry point "
                    f"{entry_point.name!r}."
                ) from exc
            if not isinstance(plugin, CapabilityPlugin):
                raise RuntimeError(
                    f"Capability entry point {entry_point.name!r} did not "
                    "return a CapabilityPlugin."
                )
            plugins.append(plugin)
    return CapabilityRegistry(plugins)
