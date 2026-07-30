from __future__ import annotations

from enum import StrEnum

from vidxp.application_models import (
    ApplicationError,
    ErrorCategory,
    Principal,
)


class RepositoryPermission(StrEnum):
    read = "vidxp.read"
    write = "vidxp.write"
    admin = "vidxp.admin"


class AuthorizationPolicy:
    """Repository-wide authorization shared by HTTP and future MCP adapters."""

    def require(
        self,
        principal: Principal,
        permission: RepositoryPermission,
    ) -> Principal:
        scopes = principal.scopes
        if (
            "*" in scopes
            or RepositoryPermission.admin.value in scopes
            or permission.value in scopes
        ):
            return principal
        raise ApplicationError(
            "insufficient_scope",
            ErrorCategory.authorization,
            "The authenticated principal lacks the required repository scope.",
            details={"required_scope": permission.value},
        )
