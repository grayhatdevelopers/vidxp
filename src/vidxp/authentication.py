from __future__ import annotations

from dataclasses import dataclass
from hmac import compare_digest
from typing import Any, Protocol

from vidxp.application_models import (
    ApplicationError,
    ComponentReadiness,
    ErrorCategory,
    Principal,
)
from vidxp.settings import HttpAuthMode, VidXPSettings


class Authenticator(Protocol):
    def authenticate(self, token: str | None) -> Principal: ...

    def readiness(self) -> ComponentReadiness: ...


@dataclass(frozen=True)
class AuthenticatedBearer:
    principal: Principal
    expires_at: int | None
    resource: str | None
    claims: dict[str, Any]


def _ready() -> ComponentReadiness:
    return ComponentReadiness(
        name="authentication",
        ready=True,
        message="The HTTP authentication profile is ready.",
    )


def _authentication_error() -> ApplicationError:
    return ApplicationError(
        "authentication_required",
        ErrorCategory.authentication,
        "Valid bearer authentication is required.",
    )


def _authorization_error() -> ApplicationError:
    return ApplicationError(
        "insufficient_scope",
        ErrorCategory.authorization,
        "The authenticated principal lacks a required scope.",
    )


class LocalAuthenticator:
    def authenticate(self, token: str | None) -> Principal:
        del token
        return Principal(subject="local", scopes=frozenset({"*"}))

    def readiness(self) -> ComponentReadiness:
        return _ready()


class StaticBearerAuthenticator:
    def __init__(self, expected_token: str) -> None:
        self._expected_token = expected_token

    def authenticate(self, token: str | None) -> Principal:
        if token is None or not compare_digest(token, self._expected_token):
            raise _authentication_error()
        return Principal(
            subject="static",
            client_id="static",
            scopes=frozenset({"*"}),
        )

    def readiness(self) -> ComponentReadiness:
        return _ready()


class OIDCBearerAuthenticator:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        algorithms: tuple[str, ...],
        required_scopes: tuple[str, ...],
        jwks_client: Any | None = None,
    ) -> None:
        import jwt

        self._jwt = jwt
        self._issuer = issuer
        self._audience = audience
        self._algorithms = algorithms
        self._required_scopes = frozenset(required_scopes)
        self._jwks = (
            jwks_client
            if jwks_client is not None
            else jwt.PyJWKClient(
                jwks_url,
                cache_keys=True,
                cache_jwk_set=True,
                lifespan=300,
                timeout=5,
            )
        )
        self._jwks_url = jwks_url

    def for_audience(
        self,
        audience: str,
        *,
        required_scopes: tuple[str, ...] = (),
    ) -> "OIDCBearerAuthenticator":
        return OIDCBearerAuthenticator(
            issuer=self._issuer,
            audience=audience,
            jwks_url=self._jwks_url,
            algorithms=self._algorithms,
            required_scopes=required_scopes,
            jwks_client=self._jwks,
        )

    def authenticate(self, token: str | None) -> Principal:
        return self.authenticate_bearer(token).principal

    def authenticate_bearer(
        self,
        token: str | None,
    ) -> AuthenticatedBearer:
        if token is None:
            raise _authentication_error()
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
            claims = self._jwt.decode(
                token,
                signing_key,
                algorithms=list(self._algorithms),
                audience=self._audience,
                issuer=self._issuer,
                options={
                    "require": ["exp", "iss", "aud", "sub"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iss": True,
                    "verify_aud": True,
                },
            )
        except self._jwt.PyJWTError as exc:
            raise _authentication_error() from exc
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise _authentication_error()
        scopes = _token_scopes(claims)
        if not self._required_scopes.issubset(scopes):
            raise _authorization_error()
        client_id = claims.get("client_id")
        principal = Principal(
            subject=subject,
            client_id=client_id if isinstance(client_id, str) else None,
            scopes=scopes,
        )
        expires_at = claims.get("exp")
        resource = claims.get("aud")
        return AuthenticatedBearer(
            principal=principal,
            expires_at=(
                expires_at if isinstance(expires_at, int) else None
            ),
            resource=(
                resource
                if isinstance(resource, str)
                else self._audience
            ),
            claims=dict(claims),
        )

    def readiness(self) -> ComponentReadiness:
        try:
            key_set = self._jwks.get_jwk_set()
            usable = any(
                self._usable_signing_key(key)
                for key in key_set.keys
            )
            if not usable:
                raise ValueError(
                    "The OIDC JWKS contains no usable signing keys."
                )
        except Exception:
            return ComponentReadiness(
                name="authentication",
                ready=False,
                message="The OIDC signing-key service is unavailable.",
            )
        return _ready()

    def _usable_signing_key(self, key) -> bool:
        if (
            key.public_key_use not in {None, "sig"}
            or key.algorithm_name not in self._algorithms
        ):
            return False
        data = getattr(key, "_jwk_data", {})
        key_operations = (
            data.get("key_ops") if isinstance(data, dict) else None
        )
        return (
            key_operations is None
            or (
                isinstance(key_operations, list)
                and "verify" in key_operations
            )
        )


def _token_scopes(claims: dict) -> frozenset[str]:
    scope = claims.get("scope")
    if isinstance(scope, str):
        values = scope.split()
    else:
        scp = claims.get("scp")
        if isinstance(scp, str):
            values = scp.split()
        elif isinstance(scp, list) and all(
            isinstance(value, str) for value in scp
        ):
            values = scp
        else:
            values = []
    return frozenset(value for value in values if value)


def create_authenticator(
    settings: VidXPSettings,
    *,
    audience: str | None = None,
    required_scopes: tuple[str, ...] | None = None,
) -> Authenticator:
    if settings.http_auth_mode == HttpAuthMode.none:
        return LocalAuthenticator()
    if settings.http_auth_mode == HttpAuthMode.static:
        assert settings.http_static_bearer_token is not None
        return StaticBearerAuthenticator(
            settings.http_static_bearer_token.get_secret_value()
        )
    assert settings.http_oidc_issuer is not None
    assert settings.http_oidc_audience is not None
    assert settings.http_oidc_jwks_url is not None
    return OIDCBearerAuthenticator(
        issuer=settings.http_oidc_issuer,
        audience=audience or settings.http_oidc_audience,
        jwks_url=settings.http_oidc_jwks_url,
        algorithms=settings.http_oidc_algorithms,
        required_scopes=(
            settings.http_required_scopes
            if required_scopes is None
            else required_scopes
        ),
    )
