from __future__ import annotations

from vidxp.application_models import ComponentReadiness, RuntimeReadiness
from vidxp.authentication import Authenticator
from vidxp.control_plane import ControlPlaneApplication
from vidxp.job_service import JobService


class ReadinessService:
    """Transport-neutral aggregate health over injected application services."""

    def __init__(
        self,
        *,
        application: ControlPlaneApplication,
        jobs: JobService,
        authenticator: Authenticator,
    ) -> None:
        self.application = application
        self.jobs = jobs
        self.authenticator = authenticator

    def details(self) -> RuntimeReadiness:
        try:
            application = self.application.runtime_readiness()
        except Exception:
            application = RuntimeReadiness(
                ready=False,
                runtime=None,
                components=(
                    ComponentReadiness(
                        name="application",
                        ready=False,
                        message="Application readiness is unavailable.",
                    ),
                ),
                dependencies=None,
            )
        components = self._components(application.components)
        return application.model_copy(
            update={
                "ready": all(component.ready for component in components),
                "components": tuple(components),
            }
        )

    def ready(self) -> bool:
        try:
            application = self.application.control_plane_readiness()
        except Exception:
            application = (
                ComponentReadiness(
                    name="application",
                    ready=False,
                    message="Application readiness is unavailable.",
                ),
            )
        return all(
            component.ready
            for component in self._components(application)
        )

    def _components(
        self,
        application: tuple[ComponentReadiness, ...],
    ) -> list[ComponentReadiness]:
        components = list(application)
        try:
            components.append(self.authenticator.readiness())
        except Exception:
            components.append(
                ComponentReadiness(
                    name="authentication",
                    ready=False,
                    message="Authentication readiness is unavailable.",
                )
            )
        try:
            components.append(self.jobs.readiness())
        except Exception:
            components.append(
                ComponentReadiness(
                    name="workflow",
                    ready=False,
                    message="The durable workflow database is unavailable.",
                )
            )
        return components
