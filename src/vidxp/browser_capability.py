from __future__ import annotations

from datetime import datetime
from importlib.resources import files
from typing import Mapping
from urllib.parse import urlsplit

from fastapi import Request, Response

from vidxp.application_models import ApplicationError, ErrorCategory
from vidxp.core.media import utc_now


class BrowserCapabilitySurface:
    """Shared browser-capability origin, asset, and session-cookie boundary."""

    def __init__(
        self,
        *,
        public_url: str | None,
        package_directory: str,
        assets: Mapping[str, str],
        cookie_name: str,
        unavailable_code: str,
        unavailable_message: str,
        forbidden_code: str,
        forbidden_message: str,
    ) -> None:
        self.public_url = public_url
        self.package_directory = package_directory
        self.assets = dict(assets)
        self.cookie_name = cookie_name
        self.unavailable_code = unavailable_code
        self.unavailable_message = unavailable_message
        self.forbidden_code = forbidden_code
        self.forbidden_message = forbidden_message

    def require_same_origin(self, request: Request) -> None:
        if self.public_url is None:
            raise ApplicationError(
                self.unavailable_code,
                ErrorCategory.unavailable,
                self.unavailable_message,
            )
        parsed = urlsplit(self.public_url)
        expected = f"{parsed.scheme}://{parsed.netloc}"
        if (
            request.headers.get("origin", "").lower() != expected.lower()
            or request.headers.get("sec-fetch-site", "same-origin")
            != "same-origin"
        ):
            raise ApplicationError(
                self.forbidden_code,
                ErrorCategory.authorization,
                self.forbidden_message,
            )

    def asset(self, name: str) -> Response:
        media_type = self.assets.get(name)
        if media_type is None:
            raise ApplicationError(
                "resource_not_found",
                ErrorCategory.not_found,
                "The requested browser-capability asset was not found.",
            )
        content = (
            files("vidxp")
            .joinpath("assets", self.package_directory, name)
            .read_bytes()
        )
        return Response(content=content, media_type=media_type)

    def page(self) -> Response:
        content = (
            files("vidxp")
            .joinpath("assets", self.package_directory, "index.html")
            .read_bytes()
        )
        return Response(content=content, media_type="text/html; charset=utf-8")

    def establish_session(
        self,
        response: Response,
        *,
        token: str,
        expires_at: datetime,
        path: str,
    ) -> None:
        response.set_cookie(
            self.cookie_name,
            token,
            max_age=max(0, int((expires_at - utc_now()).total_seconds())),
            expires=expires_at,
            path=path,
            secure=True,
            httponly=True,
            samesite="strict",
        )
