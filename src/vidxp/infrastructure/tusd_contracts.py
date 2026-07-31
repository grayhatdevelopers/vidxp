from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TusdModel(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        frozen=True,
        populate_by_name=True,
    )


class TusdHTTPRequest(TusdModel):
    method: str = Field(default="", alias="Method")
    uri: str = Field(default="", alias="URI")
    headers: dict[str, list[str]] = Field(
        default_factory=dict,
        alias="Header",
    )

    def header(self, name: str) -> tuple[str, ...]:
        lowered = name.lower()
        for key, values in self.headers.items():
            if key.lower() == lowered:
                return tuple(values)
        return ()


class TusdUpload(TusdModel):
    upload_id: str = Field(default="", alias="ID")
    size: int = Field(alias="Size", ge=0)
    offset: int = Field(default=0, alias="Offset", ge=0)
    metadata: dict[str, str] = Field(
        default_factory=dict,
        alias="MetaData",
    )
    size_is_deferred: bool = Field(
        default=False,
        alias="SizeIsDeferred",
    )
    is_partial: bool = Field(default=False, alias="IsPartial")
    is_final: bool = Field(default=False, alias="IsFinal")
    partial_uploads: list[str] | None = Field(
        default=None,
        alias="PartialUploads",
    )


class TusdEvent(TusdModel):
    upload: TusdUpload = Field(alias="Upload")
    request: TusdHTTPRequest = Field(alias="HTTPRequest")


class TusdHookRequest(TusdModel):
    hook_type: Literal[
        "pre-create",
        "post-finish",
        "pre-terminate",
        "post-terminate",
    ] = Field(alias="Type")
    event: TusdEvent = Field(alias="Event")


class TusdHTTPResponse(TusdModel):
    status_code: int = Field(alias="StatusCode", ge=100, le=599)
    body: str = Field(default="", alias="Body")
    headers: dict[str, str] = Field(default_factory=dict, alias="Header")


class TusdChangeFileInfo(TusdModel):
    upload_id: str = Field(alias="ID")


class TusdHookResponse(TusdModel):
    reject_upload: bool = Field(default=False, alias="RejectUpload")
    reject_termination: bool = Field(
        default=False,
        alias="RejectTermination",
    )
    http_response: TusdHTTPResponse | None = Field(
        default=None,
        alias="HTTPResponse",
    )
    change_file_info: TusdChangeFileInfo | None = Field(
        default=None,
        alias="ChangeFileInfo",
    )

    def wire(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )
