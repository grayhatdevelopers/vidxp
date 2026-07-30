from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from pydantic import JsonValue, ValidationError

from vidxp.application_models import (
    ApplicationError,
    ErrorCategory,
    InvalidRequestError,
    ResourceNotFoundError,
)
from vidxp.artifact_service import (
    ArtifactRequestError,
    ArtifactUnavailableError,
    InvalidArtifactError,
)
from vidxp.capabilities.actor.results import ActorClusterNotFoundError
from vidxp.capabilities.contracts import CapabilityRequestError
from vidxp.core.artifacts import (
    ArtifactIntegrityError,
    ArtifactRenderError,
    ArtifactRendererUnavailableError,
)
from vidxp.core.contracts import (
    IndexCancelledError,
    IndexSchemaError,
)
from vidxp.core.media import (
    InvalidMediaError,
    MediaImportLimitError,
    MediaProbeUnavailableError,
    MediaStoreIntegrityError,
    MediaUnavailableError,
)
from vidxp.core.storage import IndexStorageUnavailableError
from vidxp.index_state import (
    IndexingInProgressError,
    IndexNotReadyError,
)
from vidxp.media_service import (
    MediaIdempotencyConflictError,
    MediaImportNotAllowedError,
)
def _validation_details(
    exc: ValidationError,
) -> list[dict[str, JsonValue]]:
    return [
        {
            "type": item["type"],
            "location": [str(part) for part in item["loc"]],
            "message": item["msg"],
        }
        for item in exc.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )
    ]


def application_boundary(handler: Callable) -> Callable:
    """Translate expected application failures once for every transport."""

    @wraps(handler)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            return handler(*args, **kwargs)
        except ApplicationError:
            raise
        except ActorClusterNotFoundError as exc:
            raise ApplicationError(
                "actor_cluster_not_found",
                ErrorCategory.not_found,
                "The requested actor cluster was not found.",
            ) from exc
        except IndexingInProgressError as exc:
            raise ApplicationError(
                "indexing_in_progress",
                ErrorCategory.conflict,
                "An indexing operation is already in progress.",
                retryable=True,
            ) from exc
        except IndexNotReadyError as exc:
            raise ApplicationError(
                "index_not_ready",
                ErrorCategory.conflict,
                "The index is not ready.",
                retryable=True,
            ) from exc
        except IndexSchemaError as exc:
            raise ApplicationError(
                "index_schema_incompatible",
                ErrorCategory.conflict,
                "The index schema is incompatible with this version.",
            ) from exc
        except IndexStorageUnavailableError as exc:
            raise ApplicationError(
                "index_storage_unavailable",
                ErrorCategory.unavailable,
                "The remote index storage is unavailable.",
                retryable=True,
            ) from exc
        except IndexCancelledError as exc:
            raise ApplicationError(
                "operation_cancelled",
                ErrorCategory.cancelled,
                "The operation was cancelled.",
            ) from exc
        except ValidationError as exc:
            raise InvalidRequestError(
                errors=_validation_details(exc),
            ) from exc
        except CapabilityRequestError as exc:
            raise InvalidRequestError() from exc
        except InvalidMediaError as exc:
            raise ApplicationError(
                "media_invalid",
                ErrorCategory.validation,
                "The selected file is not a valid supported video.",
            ) from exc
        except MediaImportLimitError as exc:
            raise ApplicationError(
                "media_too_large",
                ErrorCategory.resource_limit,
                "The selected media exceeds the configured import limit.",
            ) from exc
        except MediaProbeUnavailableError as exc:
            raise ApplicationError(
                "media_probe_unavailable",
                ErrorCategory.unavailable,
                "The media probe dependency is unavailable. "
                "For a local installation, run `vidxp init` and retry.",
                details={"remediation": "vidxp init"},
                retryable=True,
            ) from exc
        except MediaImportNotAllowedError as exc:
            raise ApplicationError(
                "media_import_forbidden",
                ErrorCategory.authorization,
                "The selected local media cannot be imported.",
            ) from exc
        except MediaIdempotencyConflictError as exc:
            raise ApplicationError(
                "idempotency_key_reused",
                ErrorCategory.validation,
                "The idempotency key was already used for other media.",
            ) from exc
        except MediaStoreIntegrityError as exc:
            raise ApplicationError(
                "media_integrity_failed",
                ErrorCategory.conflict,
                "The media changed or failed an integrity check.",
            ) from exc
        except MediaUnavailableError as exc:
            raise ResourceNotFoundError("media") from exc
        except ArtifactUnavailableError as exc:
            raise ResourceNotFoundError("artifact") from exc
        except ArtifactIntegrityError as exc:
            raise ApplicationError(
                "artifact_integrity_failed",
                ErrorCategory.conflict,
                "The artifact failed an integrity check.",
            ) from exc
        except ArtifactRendererUnavailableError as exc:
            raise ApplicationError(
                "artifact_renderer_unavailable",
                ErrorCategory.unavailable,
                "The configured artifact renderer is unavailable. "
                "For a local installation, run `vidxp init` and retry.",
                details={"remediation": "vidxp init"},
                retryable=True,
            ) from exc
        except ArtifactRenderError as exc:
            raise ApplicationError(
                "artifact_render_failed",
                ErrorCategory.internal,
                "The requested artifact could not be rendered.",
            ) from exc
        except ArtifactRequestError as exc:
            raise InvalidRequestError() from exc
        except InvalidArtifactError as exc:
            raise ApplicationError(
                "artifact_render_invalid",
                ErrorCategory.internal,
                "The generated artifact failed media validation.",
            ) from exc

    return wrapped
