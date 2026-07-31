from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from vidxp.app_paths import default_config_directory


MEDIA_RUNTIME_SCHEMA_VERSION = 1
MEDIA_RUNTIME_FILENAME = "media-runtime.json"
REQUIRED_FFMPEG_ENCODERS = ("libx264", "aac")


class MediaRuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MediaRuntimeConfiguration(MediaRuntimeModel):
    schema_version: Literal[MEDIA_RUNTIME_SCHEMA_VERSION] = (
        MEDIA_RUNTIME_SCHEMA_VERSION
    )
    ffmpeg_executable: Path
    ffprobe_executable: Path


class SystemInstallPlan(MediaRuntimeModel):
    manager: str = Field(min_length=1)
    command: tuple[str, ...] = Field(min_length=1)
    automatic: bool

    @property
    def display_command(self) -> str:
        if sys.platform == "win32":
            return subprocess.list2cmdline(self.command)
        return shlex.join(self.command)


class MediaRuntimeStatus(MediaRuntimeModel):
    ready: bool
    initialized: bool
    ffmpeg_executable: Path | None = None
    ffprobe_executable: Path | None = None
    required_encoders: tuple[str, ...] = REQUIRED_FFMPEG_ENCODERS
    errors: tuple[str, ...] = ()
    install_plan: SystemInstallPlan | None = None


def media_runtime_config_path(
    config_directory: Path | None = None,
) -> Path:
    return (config_directory or default_config_directory()) / (
        MEDIA_RUNTIME_FILENAME
    )


def load_media_runtime_configuration(
    config_directory: Path | None = None,
) -> MediaRuntimeConfiguration | None:
    path = media_runtime_config_path(config_directory)
    try:
        contents = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    return MediaRuntimeConfiguration.model_validate_json(contents)


def default_media_executable(name: Literal["ffmpeg", "ffprobe"]) -> str:
    try:
        configuration = load_media_runtime_configuration()
    except (OSError, ValueError):
        return name
    if configuration is None:
        return name
    return str(getattr(configuration, f"{name}_executable"))


def _resolve_executable(value: str | Path) -> Path | None:
    candidate = Path(value).expanduser()
    if candidate.is_absolute() or candidate.parent != Path("."):
        return candidate.resolve() if candidate.is_file() else None
    resolved = shutil.which(str(value))
    return Path(resolved).resolve() if resolved else None


def _command_output(arguments: list[str], *, timeout: float = 15) -> str:
    completed = subprocess.run(
        arguments,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    return completed.stdout.decode(errors="replace")


def _verify_ffmpeg(path: Path) -> tuple[str, ...]:
    errors = []
    try:
        _command_output([str(path), "-version"])
        encoders = _command_output([str(path), "-hide_banner", "-encoders"])
    except (OSError, subprocess.SubprocessError) as exc:
        return (f"FFmpeg could not be executed: {exc}",)
    available = {
        token
        for line in encoders.splitlines()
        for token in line.split()
    }
    missing = tuple(
        encoder
        for encoder in REQUIRED_FFMPEG_ENCODERS
        if encoder not in available
    )
    if missing:
        errors.append(
            "FFmpeg is missing required encoder(s): " + ", ".join(missing)
        )
    return tuple(errors)


def _verify_ffprobe(path: Path) -> tuple[str, ...]:
    try:
        _command_output([str(path), "-version"])
    except (OSError, subprocess.SubprocessError) as exc:
        return (f"ffprobe could not be executed: {exc}",)
    return ()


def system_install_plan() -> SystemInstallPlan | None:
    if sys.platform == "win32":
        if shutil.which("winget"):
            return SystemInstallPlan(
                manager="Windows Package Manager",
                command=(
                    "winget",
                    "install",
                    "--id",
                    "Gyan.FFmpeg",
                    "--exact",
                    "--source",
                    "winget",
                    "--accept-package-agreements",
                    "--accept-source-agreements",
                ),
                automatic=True,
            )
        return None
    if sys.platform == "darwin":
        brew = shutil.which("brew")
        if brew is None:
            for candidate in (
                Path("/opt/homebrew/bin/brew"),
                Path("/usr/local/bin/brew"),
            ):
                if candidate.is_file():
                    brew = str(candidate)
                    break
        if brew:
            return SystemInstallPlan(
                manager="Homebrew",
                command=(brew, "install", "ffmpeg"),
                automatic=True,
            )
        return None
    if shutil.which("apt-get"):
        return SystemInstallPlan(
            manager="APT",
            command=("sudo", "apt-get", "install", "ffmpeg"),
            automatic=False,
        )
    if shutil.which("dnf"):
        return SystemInstallPlan(
            manager="DNF",
            command=("sudo", "dnf", "install", "ffmpeg"),
            automatic=False,
        )
    if shutil.which("pacman"):
        return SystemInstallPlan(
            manager="pacman",
            command=("sudo", "pacman", "-S", "ffmpeg"),
            automatic=False,
        )
    return None


def inspect_media_runtime(
    *,
    ffmpeg: str | Path | None = None,
    ffprobe: str | Path | None = None,
    config_directory: Path | None = None,
) -> MediaRuntimeStatus:
    configuration = None
    configuration_error = None
    try:
        configuration = load_media_runtime_configuration(config_directory)
    except (OSError, ValueError) as exc:
        configuration_error = f"The saved media runtime is invalid: {exc}"

    ffmpeg_value = (
        ffmpeg
        or (
            configuration.ffmpeg_executable
            if configuration is not None
            else "ffmpeg"
        )
    )
    ffprobe_value = (
        ffprobe
        or (
            configuration.ffprobe_executable
            if configuration is not None
            else "ffprobe"
        )
    )
    ffmpeg_path = _resolve_executable(ffmpeg_value)
    ffprobe_path = _resolve_executable(ffprobe_value)
    errors = []
    if configuration_error:
        errors.append(configuration_error)
    if ffmpeg_path is None:
        errors.append(f"FFmpeg was not found: {ffmpeg_value}")
    else:
        errors.extend(_verify_ffmpeg(ffmpeg_path))
    if ffprobe_path is None:
        errors.append(f"ffprobe was not found: {ffprobe_value}")
    else:
        errors.extend(_verify_ffprobe(ffprobe_path))
    return MediaRuntimeStatus(
        ready=not errors,
        initialized=configuration is not None,
        ffmpeg_executable=ffmpeg_path,
        ffprobe_executable=ffprobe_path,
        errors=tuple(errors),
        install_plan=system_install_plan() if errors else None,
    )


def save_media_runtime_configuration(
    status: MediaRuntimeStatus,
    *,
    config_directory: Path | None = None,
) -> MediaRuntimeConfiguration:
    if (
        not status.ready
        or status.ffmpeg_executable is None
        or status.ffprobe_executable is None
    ):
        raise ValueError("Only a verified media runtime can be saved.")
    configuration = MediaRuntimeConfiguration(
        ffmpeg_executable=status.ffmpeg_executable,
        ffprobe_executable=status.ffprobe_executable,
    )
    destination = media_runtime_config_path(config_directory)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{uuid4().hex}.tmp"
    )
    try:
        temporary.write_text(
            configuration.model_dump_json(indent=2),
            encoding="utf-8",
        )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return configuration


def install_media_runtime(
    plan: SystemInstallPlan,
    *,
    output_to_stderr: bool = False,
) -> None:
    if not plan.automatic:
        raise ValueError(
            "This package-manager command must be run in a system terminal."
        )
    destination = sys.stderr if output_to_stderr else None
    subprocess.run(
        list(plan.command),
        check=True,
        stdout=destination,
        stderr=destination,
    )


def explicitly_configured_by_environment() -> bool:
    return bool(
        os.environ.get("VIDXP_FFMPEG_EXECUTABLE")
        and os.environ.get("VIDXP_FFPROBE_EXECUTABLE")
    )


def media_runtime_is_initialized(
    config_directory: Path | None = None,
) -> bool:
    if explicitly_configured_by_environment():
        return True
    try:
        configuration = load_media_runtime_configuration(config_directory)
    except (OSError, ValueError):
        return False
    if bool(
        configuration is not None
        and configuration.ffmpeg_executable.is_file()
        and configuration.ffprobe_executable.is_file()
    ):
        return True
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
