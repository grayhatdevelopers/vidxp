from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from threading import RLock
from time import perf_counter
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from vidxp.capabilities.contracts import (
    CAPABILITY_CONTRACT_VERSION,
    CapabilityDefinition,
    CapabilityDependencyError,
    CapabilityExecutor,
    CapabilityPlugin,
    CapabilityProvenance,
    CapabilityRequestError,
    RuntimeCheck,
    RuntimeCheckBinding,
    capability_install_hint,
)
from vidxp.application_models import (
    CapabilityDependencyCheck,
    DependencyKind,
)
from vidxp.core.contracts import VideoSource
from vidxp.dependencies import (
    active_requirements,
    inspect_requirement,
    installed_base_requirements,
    packaged_requirements,
)
from vidxp.model_contracts import ArtifactSpec, ModelSpec
from vidxp.model_contracts import model_artifact_cached


ENTRY_POINT_GROUP = "vidxp.capabilities"


@dataclass(frozen=True)
class _RequirementBinding:
    capability: str
    provenance: CapabilityProvenance | None
    requirement: Requirement


class CapabilityRegistry:
    """Validated capability metadata with lazily constructed executors."""

    def __init__(
        self,
        plugins: Iterable[CapabilityPlugin],
        *,
        platform_runtime_checks: Iterable[RuntimeCheckBinding] = (),
        storage_requirements: Iterable[Requirement] | None = None,
    ) -> None:
        indexed: dict[str, CapabilityPlugin] = {}
        for plugin in plugins:
            if plugin.contract_version != CAPABILITY_CONTRACT_VERSION:
                raise RuntimeError(
                    f"Capability {plugin.definition.name!r} from "
                    f"{self._label(plugin)} targets contract "
                    f"{plugin.contract_version}; VidXP requires "
                    f"{CAPABILITY_CONTRACT_VERSION}."
                )
            name = plugin.definition.name
            if name in indexed:
                existing = self._label(indexed[name])
                incoming = self._label(plugin)
                raise RuntimeError(
                    f"Duplicate capability name {name!r}: "
                    f"{existing} conflicts with {incoming}."
                )
            indexed[name] = plugin
        self._plugins = MappingProxyType(indexed)
        self._platform_runtime_checks = tuple(platform_runtime_checks)
        self._storage_requirements = tuple(
            storage_requirements
            if storage_requirements is not None
            else active_requirements(
                packaged_requirements(
                    "vidxp",
                    "requirements/storage.txt",
                )
            )
        )
        self._executors: dict[str, CapabilityExecutor] = {}
        self._executor_lock = RLock()

    @staticmethod
    def _label(plugin: CapabilityPlugin) -> str:
        if plugin.provenance is None:
            return "built-in VidXP capability"
        return (
            f"{plugin.provenance.distribution}:"
            f"{plugin.provenance.entry_point}"
        )

    @property
    def definitions(self) -> Mapping[str, CapabilityDefinition]:
        return MappingProxyType(
            {name: plugin.definition for name, plugin in self._plugins.items()}
        )

    def names(self) -> tuple[str, ...]:
        return tuple(self._plugins)

    def provenance(self, name: str) -> CapabilityProvenance | None:
        self.get(name)
        return self._plugins[name].provenance

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

    def model_specs(
        self,
        names: Iterable[str] | None = None,
    ) -> tuple[ModelSpec | ArtifactSpec, ...]:
        selected = self.names() if names is None else self.validate_names(names)
        return tuple(
            dict.fromkeys(
                spec
                for name in selected
                for spec in self.get(name).model_specs
            )
        )

    def model_checks(
        self,
        names: Iterable[str],
        *,
        cache: Path,
        on_check_start: (
            Callable[[str, DependencyKind, str], None] | None
        ) = None,
        on_check_complete: (
            Callable[[CapabilityDependencyCheck, float], None] | None
        ) = None,
    ) -> tuple[CapabilityDependencyCheck, ...]:
        selected = self.validate_names(names)
        checks = []
        for name in selected:
            for spec in self.get(name).model_specs:
                if on_check_start is not None:
                    on_check_start(name, DependencyKind.model, spec.model_id)
                started = perf_counter()
                cached = model_artifact_cached(cache, spec)
                check = CapabilityDependencyCheck(
                    capability=name,
                    kind=DependencyKind.model,
                    name=spec.model_id,
                    download_size_bytes=spec.download_size_bytes,
                    ok=cached,
                    error=(
                        None
                        if cached
                        else (
                            "model artifacts are not prepared; run "
                            f"vidxp prepare --modalities {name}"
                        )
                    ),
                )
                checks.append(check)
                if on_check_complete is not None:
                    on_check_complete(check, perf_counter() - started)
        return tuple(checks)

    def get(self, name: str) -> CapabilityDefinition:
        try:
            return self._plugins[name].definition
        except KeyError as exc:
            available = ", ".join(self.names())
            raise CapabilityRequestError(
                f"Unknown capability {name!r}. "
                f"Available capabilities: {available}.",
                field="modalities",
                reason="capability_unknown",
                requested=(name,),
                available=self.names(),
                next_action="Choose a capability returned by get_workspace.",
            ) from exc

    def executor(self, name: str) -> CapabilityExecutor:
        definition = self.get(name)
        with self._executor_lock:
            if name not in self._executors:
                executor = self._plugins[name].executor_factory()
                operation_names = set(definition.operations)
                if set(executor.operations) != operation_names:
                    raise RuntimeError(
                        f"Capability {name!r} from "
                        f"{self._label(self._plugins[name])} has operation "
                        "handlers that do not match its declaration."
                    )
                if (executor.indexer is not None) != (
                    definition.collection_name is not None
                ):
                    raise RuntimeError(
                        f"Capability {name!r} from "
                        f"{self._label(self._plugins[name])} has indexing "
                        "metadata that does not match its executor."
                    )
                if (executor.prepare is not None) != definition.prepares_models:
                    raise RuntimeError(
                        f"Capability {name!r} from "
                        f"{self._label(self._plugins[name])} has preparation "
                        "metadata that does not match its executor."
                    )
                self._executors[name] = executor
        return self._executors[name]

    def validate_names(self, names: Iterable[str]) -> tuple[str, ...]:
        selected = tuple(dict.fromkeys(str(name).strip() for name in names))
        if not selected:
            raise CapabilityRequestError(
                "At least one capability is required.",
                field="modalities",
                reason="capability_required",
                available=self.names(),
                next_action="Choose a capability returned by get_workspace.",
            )
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
            raise CapabilityRequestError(
                "Options were supplied for disabled capabilities: "
                + ", ".join(unknown),
                field="capability_options",
                reason="capability_options_disabled",
                requested=tuple(unknown),
                available=selected,
                next_action=(
                    "Remove options for disabled capabilities or enable those "
                    "capabilities in modalities."
                ),
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
        bindings = self._requirement_bindings(names, source=source)
        unique = {
            str(binding.requirement): binding.requirement for binding in bindings
        }
        return tuple(unique.values())

    def _requirement_bindings(
        self,
        names: Iterable[str],
        *,
        source: VideoSource | None = None,
    ) -> tuple[_RequirementBinding, ...]:
        selected = self.validate_names(names)
        bindings = [
            _RequirementBinding("storage", None, requirement)
            for requirement in self._storage_requirements
            if any(self.get(name).collection_name for name in selected)
        ]
        for name in selected:
            plugin = self._plugins[name]
            capability_requirements = active_requirements(
                tuple(Requirement(value) for value in plugin.requirements)
                if plugin.provenance is not None
                else packaged_requirements(f"vidxp.capabilities.{name}")
            )
            executor = self.executor(name)
            selected_requirements = (
                capability_requirements
                if source is None
                else executor.source_requirements(source, capability_requirements)
            )
            bindings.extend(
                _RequirementBinding(name, plugin.provenance, requirement)
                for requirement in selected_requirements
            )
        unique = {
            (
                binding.capability,
                str(binding.requirement),
            ): binding
            for binding in bindings
        }
        return tuple(unique.values())

    def install_hint(self, names: Iterable[str]) -> str:
        selected = self.validate_names(names)
        external = [
            plugin.provenance.distribution
            for name in selected
            if (plugin := self._plugins[name]).provenance is not None
        ]
        builtins = [
            self.get(name).extra
            for name in selected
            if self._plugins[name].provenance is None
        ]
        commands = []
        if builtins:
            commands.append(
                capability_install_hint(",".join(dict.fromkeys(builtins)))
            )
        if external:
            commands.append(
                "Install external capability distributions with: pip install "
                + " ".join(dict.fromkeys(external))
            )
        return " ".join(commands)

    def runtime_checks_for(
        self,
        names: Iterable[str],
        *,
        source: VideoSource | None = None,
    ) -> tuple[RuntimeCheck, ...]:
        return tuple(
            binding.check
            for binding in self._runtime_check_bindings(
                names,
                source=source,
            )
        )

    def _runtime_check_bindings(
        self,
        names: Iterable[str],
        *,
        source: VideoSource | None = None,
    ) -> tuple[RuntimeCheckBinding, ...]:
        selected = self.validate_names(names)
        bindings = [
            binding
            for binding in self._platform_runtime_checks
            if any(self.get(name).collection_name for name in selected)
            and binding.check.applies(source)
        ]
        for name in selected:
            plugin = self._plugins[name]
            bindings.extend(
                RuntimeCheckBinding(
                    capability=name,
                    provenance=plugin.provenance,
                    check=check,
                )
                for check in self.executor(name).runtime_checks
                if check.applies(source)
            )
        unique = {
            (
                binding.capability,
                binding.check.label,
                (
                    binding.provenance.distribution,
                    binding.provenance.entry_point,
                )
                if binding.provenance is not None
                else None,
            ): binding
            for binding in bindings
        }
        return tuple(unique.values())

    def dependency_checks(
        self,
        names: Iterable[str],
        *,
        source: VideoSource | None = None,
        include_runtime_checks: bool = True,
        on_check_start: (
            Callable[[str, DependencyKind, str], None] | None
        ) = None,
        on_check_complete: (
            Callable[[CapabilityDependencyCheck, float], None] | None
        ) = None,
    ) -> tuple[CapabilityDependencyCheck, ...]:
        selected = self.validate_names(names)
        checks = []
        for binding in self._requirement_bindings(selected, source=source):
            if on_check_start is not None:
                on_check_start(
                    binding.capability,
                    DependencyKind.distribution,
                    binding.requirement.name,
                )
            started = perf_counter()
            result = inspect_requirement(binding.requirement)
            check = CapabilityDependencyCheck(
                capability=binding.capability,
                provenance=binding.provenance,
                kind=DependencyKind.distribution,
                **result,
            )
            checks.append(check)
            if on_check_complete is not None:
                on_check_complete(check, perf_counter() - started)
        if include_runtime_checks:
            for binding in self._runtime_check_bindings(
                selected,
                source=source,
            ):
                if on_check_start is not None:
                    on_check_start(
                        binding.capability,
                        DependencyKind.runtime,
                        binding.check.label,
                    )
                started = perf_counter()
                result = binding.check.inspect()
                check = CapabilityDependencyCheck(
                    capability=binding.capability,
                    provenance=binding.provenance,
                    kind=DependencyKind.runtime,
                    name=result["name"],
                    ok=result["ok"],
                    error=result["error"],
                )
                checks.append(check)
                if on_check_complete is not None:
                    on_check_complete(
                        check,
                        perf_counter() - started,
                    )
        return tuple(checks)

    def require_dependencies(
        self,
        names: Iterable[str],
        *,
        source: VideoSource,
    ) -> None:
        selected = self.validate_names(names)
        failures = tuple(
            check
            for check in self.dependency_checks(selected, source=source)
            if not check.ok
        )
        if failures:
            raise CapabilityDependencyError(
                selected,
                tuple(
                    check.model_dump(mode="json")
                    for check in failures
                ),
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
    from vidxp.capabilities.speech.definition import PLUGIN as speech
    from vidxp.capabilities.scene.definition import PLUGIN as scene
    from vidxp.capabilities.sound.definition import PLUGIN as sound
    from vidxp.capabilities.action.definition import PLUGIN as action

    return speech, sound, scene, actor, action


def _external_entry_points(allowlist: tuple[str, ...]) -> tuple[EntryPoint, ...]:
    allowed = {
        tuple(canonicalize_name(part) for part in value.split(":", 1))
        for value in allowlist
    }
    candidates = entry_points(group=ENTRY_POINT_GROUP)
    return tuple(
        sorted(
            (
                entry_point
                for entry_point in candidates
                if entry_point.dist is not None
                and (
                    canonicalize_name(entry_point.dist.name),
                    canonicalize_name(entry_point.name),
                )
                in allowed
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
    platform_runtime_checks: tuple[RuntimeCheckBinding, ...] = (),
    storage_requirements: Iterable[Requirement] | None = None,
) -> CapabilityRegistry:
    plugins = list(_builtin_plugins())
    if external:
        for entry_point in _external_entry_points(allowlist):
            distribution = entry_point.dist
            identity = (
                f"{distribution.name}:{entry_point.name}"
                if distribution is not None
                else entry_point.name
            )
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
                    f"{identity!r}."
                ) from exc
            if not isinstance(plugin, CapabilityPlugin):
                raise RuntimeError(
                    f"Capability entry point {identity!r} did not "
                    "return a CapabilityPlugin."
                )
            plugin = plugin.model_copy(
                update={
                    "provenance": CapabilityProvenance(
                        distribution=(
                            distribution.name
                            if distribution is not None
                            else "unknown"
                        ),
                        entry_point=entry_point.name,
                        version=(
                            distribution.version
                            if distribution is not None
                            else None
                        ),
                    )
                }
            )
            plugins.append(plugin)
    return CapabilityRegistry(
        plugins,
        platform_runtime_checks=platform_runtime_checks,
        storage_requirements=storage_requirements,
    )
