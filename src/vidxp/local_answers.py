from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import tarfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from vidxp.app_paths import default_config_directory, default_data_directory


LOCAL_ANSWERS_CONFIGURATION_SCHEMA_VERSION = 1
LOCAL_ANSWERS_CONFIGURATION_FILENAME = "local-answers.json"
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"
OLLAMA_HOST = "127.0.0.1:11434"
_DOWNLOAD_BUFFER_BYTES = 1024 * 1024


class LocalAnswerError(RuntimeError):
    """Raised when the configured local-answer runtime cannot be prepared."""


class LocalAnswerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ManagedRuntimeArtifact(LocalAnswerModel):
    url: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    download_size_bytes: int = Field(gt=0)
    archive: Literal["zip", "tar_gz"]
    executable: Path


class ManagedRuntimeSpec(LocalAnswerModel):
    version: str = Field(pattern=r"^[A-Za-z0-9._-]+$")
    maximum_download_size_bytes: int = Field(gt=0)
    artifacts: dict[str, ManagedRuntimeArtifact]


class LocalAnswerDefaults(LocalAnswerModel):
    context_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    temperature: float = Field(ge=0)
    top_p: float = Field(gt=0, le=1)
    presence_penalty: float = Field(ge=-2, le=2)


class LocalAnswerSpec(LocalAnswerModel):
    schema_version: Literal[1] = 1
    engine: Literal["ollama"]
    model: str = Field(min_length=1)
    download_size_bytes: int = Field(gt=0)
    managed_runtime: ManagedRuntimeSpec
    defaults: LocalAnswerDefaults
    label: str = Field(min_length=1)
    description: str = Field(min_length=1)


class LocalAnswerConfiguration(LocalAnswerModel):
    schema_version: Literal[LOCAL_ANSWERS_CONFIGURATION_SCHEMA_VERSION] = (
        LOCAL_ANSWERS_CONFIGURATION_SCHEMA_VERSION
    )
    base_url: str
    model: str = Field(min_length=1)
    executable: Path | None = None
    model_directory: Path | None = None


class LocalAnswerStatus(LocalAnswerModel):
    ready: bool
    service_ready: bool
    model_ready: bool
    configured: bool
    base_url: str
    model: str
    runtime_version: str | None = None
    executable: Path | None = None
    errors: tuple[str, ...] = ()


ProgressCallback = Callable[[dict[str, object]], None]


class ManagedOllamaSession:
    """Start a saved Ollama executable only while a VidXP process needs it."""

    def __init__(
        self,
        configuration: LocalAnswerConfiguration,
        *,
        context_tokens: int | None = None,
    ) -> None:
        self.configuration = configuration
        self.context_tokens = context_tokens or local_answer_spec().defaults.context_tokens
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def ensure_started(self) -> None:
        with self._lock:
            if inspect_local_answers(
                base_url=self.configuration.base_url,
                model=self.configuration.model,
            ).service_ready:
                return
            if self._process is not None and self._process.poll() is not None:
                self._process = None
            executable = self.configuration.executable
            model_directory = self.configuration.model_directory
            if executable is None or model_directory is None:
                raise LocalAnswerError(
                    "The configured Ollama service is unavailable and has no managed executable."
                )
            model_directory.mkdir(parents=True, exist_ok=True)
            environment = os.environ.copy()
            environment.update(
                {
                    "OLLAMA_HOST": OLLAMA_HOST,
                    "OLLAMA_MODELS": str(model_directory),
                    "OLLAMA_CONTEXT_LENGTH": str(self.context_tokens),
                    "OLLAMA_FLASH_ATTENTION": "1",
                    "OLLAMA_KV_CACHE_TYPE": "q8_0",
                    "OLLAMA_NUM_PARALLEL": "1",
                }
            )
            process = subprocess.Popen(
                [str(executable), "serve"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=environment,
            )
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise LocalAnswerError(
                        "The local answer runtime exited before becoming healthy."
                    )
                if inspect_local_answers(
                    base_url=self.configuration.base_url,
                    model=self.configuration.model,
                ).service_ready:
                    self._process = process
                    return
                time.sleep(0.15)
            process.terminate()
            process.wait(timeout=5)
            raise LocalAnswerError(
                "The local answer runtime did not become healthy within 30 seconds."
            )

    def close(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
            if process is None or process.poll() is not None:
                return
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def local_answer_spec() -> LocalAnswerSpec:
    resource = files("vidxp").joinpath("assets/local-answers.json")
    return LocalAnswerSpec.model_validate_json(resource.read_text(encoding="utf-8"))


def local_answer_config_path(
    config_directory: Path | None = None,
) -> Path:
    return (config_directory or default_config_directory()) / (
        LOCAL_ANSWERS_CONFIGURATION_FILENAME
    )


def load_local_answer_configuration(
    config_directory: Path | None = None,
) -> LocalAnswerConfiguration | None:
    path = local_answer_config_path(config_directory)
    try:
        contents = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    return LocalAnswerConfiguration.model_validate_json(contents)


def save_local_answer_configuration(
    configuration: LocalAnswerConfiguration,
    *,
    config_directory: Path | None = None,
) -> Path:
    destination = local_answer_config_path(config_directory)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            configuration.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def configured_local_answer_values() -> dict[str, str]:
    try:
        configuration = load_local_answer_configuration()
    except (OSError, ValueError):
        return {}
    if configuration is None:
        return {}
    return {
        "slm_base_url": configuration.base_url,
        "slm_model": configuration.model,
    }


def _management_root(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/v1"
        or parsed.hostname.lower() in {"ollama.com", "www.ollama.com"}
    ):
        raise ValueError(
            "The local-answer URL must be a self-hosted Ollama HTTP URL ending in /v1."
        )
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("The local-answer URL contains an invalid port.") from exc
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _request(
    url: str,
    *,
    timeout: float,
    payload: dict[str, object] | None = None,
):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "VidXP",
        },
        method="GET" if data is None else "POST",
    )
    return urllib.request.urlopen(request, timeout=timeout)


def _json_response(url: str, *, timeout: float) -> object:
    with _request(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _model_matches(candidate: object, model: str) -> bool:
    if not isinstance(candidate, dict):
        return False
    name = candidate.get("name")
    digest = candidate.get("digest")
    if not isinstance(name, str) or not isinstance(digest, str) or not digest.strip():
        return False
    return name == model or name.removesuffix(":latest") == model.removesuffix(
        ":latest"
    )


def inspect_local_answers(
    *,
    base_url: str = DEFAULT_OLLAMA_BASE_URL,
    model: str | None = None,
    configuration: LocalAnswerConfiguration | None = None,
) -> LocalAnswerStatus:
    selected_model = model or local_answer_spec().model
    errors: list[str] = []
    version: str | None = None
    model_ready = False
    try:
        root = _management_root(base_url)
        version_payload = _json_response(f"{root}/api/version", timeout=3)
        if not isinstance(version_payload, dict) or not isinstance(
            version_payload.get("version"), str
        ):
            raise ValueError("Ollama returned an invalid version response.")
        version = version_payload["version"].strip()
        if not version:
            raise ValueError("Ollama returned an empty version.")
        tags = _json_response(f"{root}/api/tags", timeout=10)
        models = tags.get("models") if isinstance(tags, dict) else None
        if not isinstance(models, list):
            raise ValueError("Ollama returned an invalid model inventory.")
        model_ready = any(_model_matches(item, selected_model) for item in models)
        if not model_ready:
            errors.append(f"The local answer model {selected_model} is not installed.")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"The local Ollama service is unavailable: {exc}")
    service_ready = version is not None
    return LocalAnswerStatus(
        ready=service_ready and model_ready,
        service_ready=service_ready,
        model_ready=model_ready,
        configured=configuration is not None,
        base_url=base_url,
        model=selected_model,
        runtime_version=version,
        executable=None if configuration is None else configuration.executable,
        errors=tuple(errors),
    )


def _platform_key() -> str | None:
    machine = platform.machine().lower()
    if os.name == "nt" and machine in {"amd64", "x86_64"}:
        return "windows-x86_64"
    if platform.system() == "Darwin" and machine in {"arm64", "aarch64"}:
        return "macos-aarch64"
    return None


def _platform_error() -> str | None:
    if platform.system() == "Darwin":
        try:
            major = int(platform.mac_ver()[0].split(".", 1)[0])
        except (ValueError, IndexError):
            return None
        if major < 14:
            return "Local grounded answers require macOS 14 or newer."
    if os.name == "nt":
        try:
            parts = tuple(int(part) for part in platform.version().split("."))
        except ValueError:
            return None
        if parts < (10, 0, 19045):
            return "Local grounded answers require Windows 10 22H2 or newer."
    return None


def managed_runtime_directory(
    runtime_root: Path,
    spec: ManagedRuntimeSpec,
) -> Path:
    return runtime_root / "query-runtimes" / f"ollama-{spec.version}"


def managed_ollama_executable(
    runtime_root: Path,
    spec: ManagedRuntimeSpec | None = None,
) -> Path | None:
    selected = spec or local_answer_spec().managed_runtime
    key = _platform_key()
    artifact = None if key is None else selected.artifacts.get(key)
    if artifact is None:
        return None
    candidate = managed_runtime_directory(runtime_root, selected) / artifact.executable
    return candidate.resolve() if candidate.is_file() else None


def _safe_archive_path(value: str) -> Path:
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts:
        raise LocalAnswerError("The managed Ollama archive contains an unsafe path.")
    return Path(*pure.parts)


def _safe_archive_link(member: Path, value: str, *, relative: bool) -> Path:
    target = PurePosixPath(value)
    if target.is_absolute():
        raise LocalAnswerError("The managed Ollama archive contains an unsafe link.")
    combined = PurePosixPath(member.parent.as_posix()) / target if relative else target
    parts: list[str] = []
    for part in combined.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                raise LocalAnswerError(
                    "The managed Ollama archive contains an unsafe link."
                )
            parts.pop()
        else:
            parts.append(part)
    if not parts:
        raise LocalAnswerError("The managed Ollama archive contains an unsafe link.")
    return Path(*parts)


def _extract_archive(archive: Path, destination: Path, kind: str) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if kind == "zip":
        with zipfile.ZipFile(archive) as opened:
            for info in opened.infolist():
                relative = _safe_archive_path(info.filename)
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise LocalAnswerError(
                        "The managed Ollama ZIP archive contains an unsupported link."
                    )
                output = destination / relative
                if info.is_dir():
                    output.mkdir(parents=True, exist_ok=True)
                    continue
                output.parent.mkdir(parents=True, exist_ok=True)
                with opened.open(info) as source, output.open("wb") as target:
                    shutil.copyfileobj(source, target)
                if mode:
                    output.chmod(mode & 0o777)
        return
    pending_links: list[tuple[Path, Path]] = []
    with tarfile.open(archive, "r:gz") as opened:
        for member in opened:
            relative = _safe_archive_path(member.name)
            output = destination / relative
            if member.issym() or member.islnk():
                target = _safe_archive_link(
                    relative,
                    member.linkname,
                    relative=member.issym(),
                )
                pending_links.append((output, destination / target))
                continue
            if not (member.isfile() or member.isdir()):
                raise LocalAnswerError(
                    "The managed Ollama archive contains an unsupported entry."
                )
            if member.isdir():
                output.mkdir(parents=True, exist_ok=True)
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            source = opened.extractfile(member)
            if source is None:
                raise LocalAnswerError("The managed Ollama archive is incomplete.")
            with source, output.open("wb") as target:
                shutil.copyfileobj(source, target)
            output.chmod(member.mode & 0o777)
    while pending_links:
        remaining = []
        for output, target in pending_links:
            if not target.is_file():
                remaining.append((output, target))
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, output)
        if len(remaining) == len(pending_links):
            raise LocalAnswerError(
                "The managed Ollama archive contains a link to a missing or non-file target."
            )
        pending_links = remaining


def install_managed_ollama(
    runtime_root: Path,
    *,
    progress: ProgressCallback | None = None,
) -> Path:
    spec = local_answer_spec().managed_runtime
    existing = managed_ollama_executable(runtime_root, spec)
    if existing is not None:
        return existing
    key = _platform_key()
    artifact = None if key is None else spec.artifacts.get(key)
    if artifact is None:
        raise LocalAnswerError(
            "VidXP does not publish a managed Ollama runtime for this operating system and architecture."
        )
    root = runtime_root / "query-runtimes"
    root.mkdir(parents=True, exist_ok=True)
    target = managed_runtime_directory(runtime_root, spec)
    nonce = uuid4().hex
    archive = root / f".ollama-{spec.version}-{nonce}.download"
    staging = root / f".ollama-{spec.version}-{nonce}.partial"
    try:
        request = urllib.request.Request(
            artifact.url,
            headers={"User-Agent": "VidXP"},
        )
        with urllib.request.urlopen(request, timeout=2 * 60 * 60) as response, archive.open(
            "wb"
        ) as output:
            digest = hashlib.sha256()
            downloaded = 0
            while chunk := response.read(_DOWNLOAD_BUFFER_BYTES):
                output.write(chunk)
                digest.update(chunk)
                downloaded += len(chunk)
                if progress is not None:
                    progress(
                        {
                            "stage": "local_answer_runtime",
                            "message": "Downloading the managed Ollama runtime.",
                            "current": downloaded,
                            "total": artifact.download_size_bytes,
                        }
                    )
        if downloaded != artifact.download_size_bytes:
            raise LocalAnswerError(
                f"The managed Ollama download contained {downloaded} bytes; expected {artifact.download_size_bytes}."
            )
        actual = digest.hexdigest()
        if actual != artifact.sha256:
            raise LocalAnswerError(
                "The managed Ollama runtime failed checksum verification."
            )
        _extract_archive(archive, staging, artifact.archive)
        staged = staging / artifact.executable
        if not staged.is_file():
            raise LocalAnswerError(
                f"The managed Ollama archive did not contain {artifact.executable}."
            )
        if target.exists():
            shutil.rmtree(target)
        staging.replace(target)
        executable = target / artifact.executable
        return executable.resolve()
    except (OSError, urllib.error.URLError, zipfile.BadZipFile, tarfile.TarError) as exc:
        raise LocalAnswerError(f"Could not install the managed Ollama runtime: {exc}") from exc
    finally:
        archive.unlink(missing_ok=True)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def _system_ollama() -> Path | None:
    resolved = shutil.which("ollama")
    if resolved:
        return Path(resolved).resolve()
    candidates = []
    if platform.system() == "Darwin":
        candidates.append(
            Path("/Applications/Ollama.app/Contents/Resources/ollama")
        )
    elif os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        candidates.append(
            Path(os.environ["LOCALAPPDATA"]) / "Programs/Ollama/ollama.exe"
        )
    return next((item.resolve() for item in candidates if item.is_file()), None)


@contextmanager
def _available_service(
    *,
    base_url: str,
    executable: Path,
    model_directory: Path,
) -> Iterator[None]:
    session = ManagedOllamaSession(
        LocalAnswerConfiguration(
            base_url=base_url,
            model=local_answer_spec().model,
            executable=executable,
            model_directory=model_directory,
        )
    )
    try:
        session.ensure_started()
        yield
    finally:
        session.close()


def _pull_model(
    *,
    base_url: str,
    model: str,
    progress: ProgressCallback | None,
) -> None:
    root = _management_root(base_url)
    try:
        with _request(
            f"{root}/api/pull",
            timeout=2 * 60 * 60,
            payload={"model": model, "stream": True},
        ) as response:
            for raw_line in response:
                if not raw_line.strip():
                    continue
                event = json.loads(raw_line.decode("utf-8"))
                if not isinstance(event, dict):
                    raise LocalAnswerError(
                        "Ollama returned invalid model download progress."
                    )
                if event.get("error"):
                    raise LocalAnswerError(
                        f"Ollama could not download {model}: {event['error']}"
                    )
                if progress is not None:
                    progress(
                        {
                            "stage": "local_answer_model",
                            "message": str(event.get("status") or "Downloading model."),
                            "current": event.get("completed"),
                            "total": event.get("total"),
                        }
                    )
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise LocalAnswerError(f"Could not download {model}: {exc}") from exc


def prepare_local_answers(
    *,
    data_directory: Path | None = None,
    runtime_root: Path | None = None,
    base_url: str = DEFAULT_OLLAMA_BASE_URL,
    model: str | None = None,
    executable: Path | None = None,
    save: bool = True,
    progress: ProgressCallback | None = None,
) -> LocalAnswerConfiguration:
    if error := _platform_error():
        raise LocalAnswerError(error)
    selected_data = (data_directory or default_data_directory()).expanduser()
    selected_runtime_root = (runtime_root or selected_data).expanduser()
    selected_model = model or local_answer_spec().model
    _management_root(base_url)
    initial_status = inspect_local_answers(base_url=base_url, model=selected_model)
    selected_executable: Path | None = None
    model_directory: Path | None = None
    service = nullcontext()
    if not initial_status.service_ready:
        selected_executable = executable or _system_ollama()
        if selected_executable is None:
            selected_executable = install_managed_ollama(
                selected_runtime_root,
                progress=progress,
            )
        selected_executable = selected_executable.expanduser().resolve()
        if not selected_executable.is_file():
            raise LocalAnswerError(
                f"The Ollama executable does not exist: {selected_executable}"
            )
        model_directory = selected_data / "models" / "ollama"
        service = _available_service(
            base_url=base_url,
            executable=selected_executable,
            model_directory=model_directory,
        )
    with service:
        status = inspect_local_answers(base_url=base_url, model=selected_model)
        if not status.model_ready:
            _pull_model(
                base_url=base_url,
                model=selected_model,
                progress=progress,
            )
        ready = inspect_local_answers(base_url=base_url, model=selected_model)
        if not ready.ready:
            raise LocalAnswerError("Local grounded answers were not ready after preparation.")
    configuration = LocalAnswerConfiguration(
        base_url=base_url,
        model=selected_model,
        executable=selected_executable,
        model_directory=model_directory,
    )
    if save:
        save_local_answer_configuration(configuration)
    return configuration
