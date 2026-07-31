from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Sequence

from vidxp.application_models import ApplicationError, ErrorCategory
from vidxp.benchmarks.didemo import (
    DIDEMO_EVALUATOR_SHA256,
    DIDEMO_REVISION,
    DIDEMO_TEST_SHA256,
    DIDEMO_VALIDATION_SHA256,
    parse_annotations,
    select_annotations,
)
from vidxp.benchmarks.hirest import (
    HIREST_ASR_SHA256,
    HIREST_ASR_URL,
    HIREST_CATEGORIES_SHA256,
    HIREST_EVALUATOR_SHA256,
    HIREST_REVISION,
    HIREST_TEST_SHA256,
    HIREST_VALIDATION_SHA256,
    select_ground_truth,
)
from vidxp.core.manifest import sha256_file, utc_now, write_json_atomic
from vidxp.infrastructure.local_media import FFprobeMediaProbe
from vidxp.media_runtime import inspect_media_runtime


DIDEMO_RAW_ROOT = (
    "https://raw.githubusercontent.com/LisaAnne/LocalizingMoments/"
    f"{DIDEMO_REVISION}"
)
DIDEMO_HASH_MAP_SHA256 = (
    "481d9aaf020624d5915200bcf4752fb46d3e1931167e8b46715a5f342577cc4d"
)
DIDEMO_AWS_TEMPLATE = (
    "https://multimedia-commons.s3-us-west-2.amazonaws.com/"
    "data/videos/mp4/{first}/{second}/{digest}.mp4"
)
DIDEMO_KNOWN_REPLACEMENT_VIDEO = (
    "12090392@N02_13482799053_87ef417396.mov"
)
DIDEMO_KNOWN_REPLACEMENT_URL = (
    "https://upload.wikimedia.org/wikipedia/commons/a/af/"
    "Common_Starlings_flying_away_from_a_Marsh_Harrier.webm"
)
DIDEMO_KNOWN_REPLACEMENT_SHA1 = (
    "2aefa90d4256e74cf62e492729c0e0f6d6bede72"
)
DIDEMO_KNOWN_REPLACEMENT_SIZE = 94_107_862

HIREST_RAW_ROOT = (
    "https://raw.githubusercontent.com/j-min/HiREST/"
    f"{HIREST_REVISION}"
)
HIREST_ASR_UNCOMPRESSED_SIZE = 17_236_665

ProgressCallback = Callable[[str, int], None]


@dataclass(frozen=True)
class PreparationResource:
    name: str
    url: str
    destination: Path
    size_bytes: int
    kind: Literal["artifact", "media"]
    content: bytes | None = None
    expected_sha256: str | None = None
    expected_sha1: str | None = None
    existing: bool = False
    replacement_for: str | None = None

    def public_record(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "path": str(self.destination),
            "size_bytes": self.size_bytes,
            "kind": self.kind,
            "expected_sha256": self.expected_sha256,
            "expected_sha1": self.expected_sha1,
            "existing": self.existing,
            "replacement_for": self.replacement_for,
        }


@dataclass(frozen=True)
class PreparationPlan:
    benchmark: Literal["didemo", "hirest"]
    split: Literal["validation", "test"]
    root: Path
    resources: tuple[PreparationResource, ...]
    selected_count: int
    selected_video_names: tuple[str, ...]
    command: str
    manifest_path: Path
    media_directory: Path | None = None
    media_overrides_path: Path | None = None
    asr_directory: Path | None = None
    extraction_reserve_bytes: int = 0

    @property
    def additional_bytes(self) -> int:
        return self.extraction_reserve_bytes + sum(
            self._remaining_bytes(resource) for resource in self.resources
        )

    @property
    def network_bytes(self) -> int:
        return sum(
            self._remaining_bytes(resource)
            for resource in self.resources
            if resource.content is None
        )

    @property
    def download_count(self) -> int:
        return sum(not resource.existing for resource in self.resources)

    @staticmethod
    def _remaining_bytes(resource: PreparationResource) -> int:
        if resource.existing:
            return 0
        if resource.content is not None:
            return resource.size_bytes
        partial = resource.destination.with_name(
            resource.destination.name + ".part"
        )
        partial_size = partial.stat().st_size if partial.is_file() else 0
        if partial_size > resource.size_bytes:
            return resource.size_bytes
        return resource.size_bytes - partial_size


def _application_error(
    code: str,
    message: str,
    *,
    details: Mapping[str, Any] | None = None,
) -> ApplicationError:
    return ApplicationError(
        code,
        ErrorCategory.unavailable,
        message,
        details=dict(details or {}),
    )


def _request(url: str, *, method: str = "GET", range_start: int = 0):
    headers = {"User-Agent": "VidXP benchmark preparation"}
    if range_start:
        headers["Range"] = f"bytes={range_start}-"
    return urllib.request.Request(url, headers=headers, method=method)


def _fetch_bytes(url: str) -> bytes:
    try:
        with urllib.request.urlopen(_request(url), timeout=60) as response:
            return response.read()
    except (OSError, urllib.error.URLError) as exc:
        raise _application_error(
            "benchmark_source_unavailable",
            f"Could not read pinned benchmark source: {url}",
            details={"url": url, "reason": str(exc)},
        ) from exc


def _fetch_verified(url: str, expected_sha256: str, name: str) -> bytes:
    content = _fetch_bytes(url)
    observed = hashlib.sha256(content).hexdigest()
    if observed != expected_sha256:
        raise _application_error(
            "benchmark_source_checksum_mismatch",
            f"{name} did not match its pinned SHA-256.",
            details={
                "url": url,
                "expected_sha256": expected_sha256,
                "observed_sha256": observed,
            },
        )
    return content


def _remote_size(url: str) -> int:
    try:
        with urllib.request.urlopen(
            _request(url, method="HEAD"),
            timeout=30,
        ) as response:
            value = response.headers.get("Content-Length")
            if value and int(value) > 0:
                return int(value)
    except (OSError, ValueError, urllib.error.URLError):
        pass
    try:
        with urllib.request.urlopen(
            _request(url, range_start=0),
            timeout=30,
        ) as response:
            content_range = response.headers.get("Content-Range", "")
            if "/" in content_range:
                value = int(content_range.rsplit("/", 1)[1])
                if value > 0:
                    return value
            value = response.headers.get("Content-Length")
            if value and int(value) > 0:
                return int(value)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise _application_error(
            "benchmark_download_size_unavailable",
            f"Could not determine the download size for {url}",
            details={"url": url, "reason": str(exc)},
        ) from exc
    raise _application_error(
        "benchmark_download_size_unavailable",
        f"The benchmark source did not report a download size: {url}",
        details={"url": url},
    )


def _sha1_file(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_artifact(
    path: Path,
    *,
    expected_sha256: str | None = None,
    expected_sha1: str | None = None,
) -> bool:
    if not path.is_file():
        return False
    if expected_sha256 and sha256_file(path) != expected_sha256:
        return False
    return not expected_sha1 or _sha1_file(path) == expected_sha1


def _validate_media(path: Path, *, ffprobe: str, ffmpeg: str) -> None:
    media = FFprobeMediaProbe(ffprobe).probe(path)
    seek = min(15.0, max(0.0, media.duration_seconds / 2))
    try:
        subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-ss",
                str(seek),
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-f",
                "null",
                "-",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=60,
        )
    except OSError as exc:
        raise _application_error(
            "benchmark_media_runtime_unavailable",
            "FFmpeg could not be executed while validating benchmark media.",
            details={"ffmpeg": ffmpeg, "reason": str(exc)},
        ) from exc
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError(
            f"FFmpeg could not decode a frame from {path}."
        ) from exc


def _valid_media(path: Path, *, ffprobe: str, ffmpeg: str) -> bool:
    if not path.is_file():
        return False
    try:
        _validate_media(path, ffprobe=ffprobe, ffmpeg=ffmpeg)
    except (ApplicationError, OSError, ValueError):
        return False
    return True


def _artifact_resource(
    *,
    name: str,
    url: str,
    destination: Path,
    expected_sha256: str,
    content: bytes,
) -> PreparationResource:
    return PreparationResource(
        name=name,
        url=url,
        destination=destination,
        size_bytes=len(content),
        kind="artifact",
        content=content,
        expected_sha256=expected_sha256,
        existing=_valid_artifact(
            destination,
            expected_sha256=expected_sha256,
        ),
    )


def _with_remote_sizes(
    resources: Sequence[PreparationResource],
) -> tuple[PreparationResource, ...]:
    resolved = list(resources)
    pending = {
        index: resource
        for index, resource in enumerate(resources)
        if (
            resource.content is None
            and not resource.existing
            and resource.size_bytes <= 0
        )
    }
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(_remote_size, resource.url): index
            for index, resource in pending.items()
        }
        for future in as_completed(futures):
            index = futures[future]
            resolved[index] = replace(
                pending[index],
                size_bytes=future.result(),
            )
    return tuple(resolved)


def _parse_didemo_hashes(content: bytes) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for line in content.decode("utf-8").splitlines():
        fields = line.strip().split("\t")
        if len(fields) == 2 and fields[0] and fields[1]:
            mapping[fields[0]] = fields[1]
    if not mapping:
        raise ValueError("The pinned DiDeMo YFCC hash map is empty.")
    return mapping


def _didemo_aws_url(video_name: str, hashes: Mapping[str, str]) -> str:
    fields = video_name.split("_")
    if len(fields) < 3 or not fields[1]:
        raise ValueError(f"Unsupported DiDeMo video name: {video_name}")
    try:
        digest = hashes[fields[1]]
    except KeyError as exc:
        raise ValueError(
            f"No YFCC hash exists for DiDeMo video {video_name}."
        ) from exc
    return DIDEMO_AWS_TEMPLATE.format(
        first=digest[:3],
        second=digest[3:6],
        digest=digest,
    )


def _quote_powershell(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def plan_didemo(
    *,
    root: str | Path,
    split: Literal["validation", "test"],
    annotation_indices: Sequence[int] | None,
    ffprobe: str,
    ffmpeg: str,
) -> PreparationPlan:
    runtime_status = inspect_media_runtime(
        ffprobe=ffprobe,
        ffmpeg=ffmpeg,
    )
    if not runtime_status.ready:
        raise _application_error(
            "benchmark_media_runtime_unavailable",
            "DiDeMo preparation requires FFmpeg and ffprobe to validate "
            "downloaded videos. Run `vidxp init`, then retry.",
            details={
                "errors": list(runtime_status.errors),
                "remediation": "vidxp init",
            },
        )
    destination = Path(root).expanduser().resolve()
    split_name = "val" if split == "validation" else "test"
    annotation_sha = (
        DIDEMO_VALIDATION_SHA256
        if split == "validation"
        else DIDEMO_TEST_SHA256
    )
    annotation_url = f"{DIDEMO_RAW_ROOT}/data/{split_name}_data.json"
    evaluator_url = f"{DIDEMO_RAW_ROOT}/utils/eval.py"
    hash_url = f"{DIDEMO_RAW_ROOT}/data/yfcc100m_hash.txt"
    annotation_content = _fetch_verified(
        annotation_url,
        annotation_sha,
        f"DiDeMo {split} annotations",
    )
    evaluator_content = _fetch_verified(
        evaluator_url,
        DIDEMO_EVALUATOR_SHA256,
        "DiDeMo evaluator",
    )
    hash_content = _fetch_verified(
        hash_url,
        DIDEMO_HASH_MAP_SHA256,
        "DiDeMo YFCC hash map",
    )
    annotations = select_annotations(
        parse_annotations(json.loads(annotation_content)),
        annotation_indices,
    )
    video_names = tuple(
        sorted({str(annotation["video"]) for annotation in annotations})
    )
    hashes = _parse_didemo_hashes(hash_content)
    annotations_path = destination / "annotations" / f"{split_name}_data.json"
    evaluator_path = destination / "evaluator" / "eval.py"
    hash_path = destination / "metadata" / "yfcc100m_hash.txt"
    media_directory = destination / "media"
    resources: list[PreparationResource] = [
        _artifact_resource(
            name=f"DiDeMo {split} annotations",
            url=annotation_url,
            destination=annotations_path,
            expected_sha256=annotation_sha,
            content=annotation_content,
        ),
        _artifact_resource(
            name="DiDeMo evaluator",
            url=evaluator_url,
            destination=evaluator_path,
            expected_sha256=DIDEMO_EVALUATOR_SHA256,
            content=evaluator_content,
        ),
        _artifact_resource(
            name="DiDeMo YFCC hash map",
            url=hash_url,
            destination=hash_path,
            expected_sha256=DIDEMO_HASH_MAP_SHA256,
            content=hash_content,
        ),
    ]
    overrides: dict[str, str] = {}
    for video_name in video_names:
        if video_name == DIDEMO_KNOWN_REPLACEMENT_VIDEO:
            replacement_path = (
                destination
                / "replacements"
                / "common-starlings-flickr-13482799053.webm"
            )
            overrides[video_name] = str(
                replacement_path.relative_to(destination)
            )
            resources.append(
                PreparationResource(
                    name=f"documented replacement for {video_name}",
                    url=DIDEMO_KNOWN_REPLACEMENT_URL,
                    destination=replacement_path,
                    size_bytes=DIDEMO_KNOWN_REPLACEMENT_SIZE,
                    kind="media",
                    expected_sha1=DIDEMO_KNOWN_REPLACEMENT_SHA1,
                    existing=(
                        _valid_artifact(
                            replacement_path,
                            expected_sha1=DIDEMO_KNOWN_REPLACEMENT_SHA1,
                        )
                        and _valid_media(
                            replacement_path,
                            ffprobe=ffprobe,
                            ffmpeg=ffmpeg,
                        )
                    ),
                    replacement_for=video_name,
                )
            )
            continue
        path = media_directory / video_name
        resources.append(
            PreparationResource(
                name=video_name,
                url=_didemo_aws_url(video_name, hashes),
                destination=path,
                size_bytes=0,
                kind="media",
                existing=_valid_media(
                    path,
                    ffprobe=ffprobe,
                    ffmpeg=ffmpeg,
                ),
            )
        )
    overrides_path = (
        destination / "media-overrides.json" if overrides else None
    )
    if overrides_path is not None:
        override_content = (
            json.dumps(overrides, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        override_sha = hashlib.sha256(override_content).hexdigest()
        resources.append(
            _artifact_resource(
                name="DiDeMo media overrides",
                url="documented-local-manifest",
                destination=overrides_path,
                expected_sha256=override_sha,
                content=override_content,
            )
        )
    resources = list(_with_remote_sizes(resources))
    command = (
        "vidxp benchmark didemo "
        f"--annotations {_quote_powershell(annotations_path)} "
        f"--evaluator {_quote_powershell(evaluator_path)} "
        f"--media-directory {_quote_powershell(media_directory)} "
        f"--split {split} "
    )
    if annotation_indices is not None:
        command += (
            "--annotation-indices "
            + ",".join(str(index) for index in annotation_indices)
            + " "
        )
    if overrides_path is not None:
        command += (
            f"--media-overrides {_quote_powershell(overrides_path)} "
        )
    command += f"--run-id didemo-{split}-run"
    return PreparationPlan(
        benchmark="didemo",
        split=split,
        root=destination,
        resources=tuple(resources),
        selected_count=len(annotations),
        selected_video_names=video_names,
        command=command,
        manifest_path=destination / "preparation-manifest.json",
        media_directory=media_directory,
        media_overrides_path=overrides_path,
    )


def plan_hirest(
    *,
    root: str | Path,
    split: Literal["validation", "test"],
    pairs: Sequence[tuple[str, str]] | None,
) -> PreparationPlan:
    destination = Path(root).expanduser().resolve()
    split_name = "val" if split == "validation" else "test"
    ground_truth_sha = (
        HIREST_VALIDATION_SHA256
        if split == "validation"
        else HIREST_TEST_SHA256
    )
    ground_truth_url = (
        f"{HIREST_RAW_ROOT}/data/splits/all_data_{split_name}.json"
    )
    categories_url = f"{HIREST_RAW_ROOT}/data/evaluation/categories.json"
    evaluator_url = f"{HIREST_RAW_ROOT}/evaluate.py"
    ground_truth_content = _fetch_verified(
        ground_truth_url,
        ground_truth_sha,
        f"HiREST {split} ground truth",
    )
    categories_content = _fetch_verified(
        categories_url,
        HIREST_CATEGORIES_SHA256,
        "HiREST categories",
    )
    evaluator_content = _fetch_verified(
        evaluator_url,
        HIREST_EVALUATOR_SHA256,
        "HiREST evaluator",
    )
    ground_truth = json.loads(ground_truth_content)
    _, ordered_pairs = select_ground_truth(ground_truth, pairs)
    video_names = tuple(sorted({video for _, video in ordered_pairs}))
    ground_truth_path = (
        destination / "annotations" / f"all_data_{split_name}.json"
    )
    categories_path = destination / "evaluator" / "categories.json"
    evaluator_path = destination / "evaluator" / "evaluate.py"
    asr_archive_path = destination / "ASR.zip"
    asr_directory = destination / "asr"
    resources = [
        _artifact_resource(
            name=f"HiREST {split} ground truth",
            url=ground_truth_url,
            destination=ground_truth_path,
            expected_sha256=ground_truth_sha,
            content=ground_truth_content,
        ),
        _artifact_resource(
            name="HiREST categories",
            url=categories_url,
            destination=categories_path,
            expected_sha256=HIREST_CATEGORIES_SHA256,
            content=categories_content,
        ),
        _artifact_resource(
            name="HiREST evaluator",
            url=evaluator_url,
            destination=evaluator_path,
            expected_sha256=HIREST_EVALUATOR_SHA256,
            content=evaluator_content,
        ),
        PreparationResource(
            name="HiREST released ASR",
            url=HIREST_ASR_URL,
            destination=asr_archive_path,
            size_bytes=0,
            kind="artifact",
            expected_sha256=HIREST_ASR_SHA256,
            existing=_valid_artifact(
                asr_archive_path,
                expected_sha256=HIREST_ASR_SHA256,
            ),
        ),
    ]
    pairs_path = None
    if pairs is not None:
        pairs_path = destination / "selection" / "pairs.json"
        pairs_content = (
            json.dumps(
                [
                    {"prompt": prompt, "video": video}
                    for prompt, video in pairs
                ],
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")
        pairs_sha = hashlib.sha256(pairs_content).hexdigest()
        resources.append(
            _artifact_resource(
                name="HiREST selected pairs",
                url="user-declared-selection",
                destination=pairs_path,
                expected_sha256=pairs_sha,
                content=pairs_content,
            )
        )
    resources = list(_with_remote_sizes(resources))
    command = (
        "vidxp benchmark hirest "
        f"--ground-truth {_quote_powershell(ground_truth_path)} "
        f"--categories {_quote_powershell(categories_path)} "
        f"--evaluator {_quote_powershell(evaluator_path)} "
        f"--asr-archive {_quote_powershell(asr_archive_path)} "
        f"--asr-directory {_quote_powershell(asr_directory)} "
        f"--split {split} "
    )
    if pairs_path is not None:
        command += f"--pairs {_quote_powershell(pairs_path)} "
    command += f"--run-id hirest-{split}-run"
    transcripts_ready = all(
        (asr_directory / f"{Path(video).stem}.srt").is_file()
        and (asr_directory / f"{Path(video).stem}.srt").stat().st_size > 0
        for video in video_names
    )
    return PreparationPlan(
        benchmark="hirest",
        split=split,
        root=destination,
        resources=tuple(resources),
        selected_count=len(ordered_pairs),
        selected_video_names=video_names,
        command=command,
        manifest_path=destination / "preparation-manifest.json",
        asr_directory=asr_directory,
        extraction_reserve_bytes=(
            0 if transcripts_ready else HIREST_ASR_UNCOMPRESSED_SIZE
        ),
    )


def _download_resource(
    resource: PreparationResource,
    *,
    ffprobe: str,
    ffmpeg: str,
    progress: ProgressCallback | None,
) -> str:
    if resource.existing:
        return "reused"
    resource.destination.parent.mkdir(parents=True, exist_ok=True)
    if resource.content is not None:
        temporary = resource.destination.with_name(
            resource.destination.name + ".part"
        )
        temporary.write_bytes(resource.content)
    else:
        temporary = resource.destination.with_name(
            resource.destination.name + ".part"
        )
        start = temporary.stat().st_size if temporary.is_file() else 0
        if start > resource.size_bytes:
            temporary.unlink()
            start = 0
        if start < resource.size_bytes:
            request = _request(resource.url, range_start=start)
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    status = getattr(response, "status", response.getcode())
                    if start and status != 206:
                        start = 0
                    mode = "ab" if start and status == 206 else "wb"
                    with temporary.open(mode) as stream:
                        while chunk := response.read(1024 * 1024):
                            stream.write(chunk)
                            if progress is not None:
                                progress(resource.name, len(chunk))
            except (OSError, urllib.error.URLError) as exc:
                raise _application_error(
                    "benchmark_download_failed",
                    f"Download failed for {resource.name}.",
                    details={
                        "url": resource.url,
                        "partial_path": str(temporary),
                        "reason": str(exc),
                    },
                ) from exc
    if temporary.stat().st_size != resource.size_bytes:
        raise ValueError(
            f"{resource.name} has {temporary.stat().st_size} bytes; "
            f"expected {resource.size_bytes}."
        )
    if resource.expected_sha256:
        observed = sha256_file(temporary)
        if observed != resource.expected_sha256:
            raise ValueError(
                f"{resource.name} SHA-256 was {observed}; "
                f"expected {resource.expected_sha256}."
            )
    if resource.expected_sha1:
        observed = _sha1_file(temporary)
        if observed != resource.expected_sha1:
            raise ValueError(
                f"{resource.name} SHA-1 was {observed}; "
                f"expected {resource.expected_sha1}."
            )
    if resource.kind == "media":
        _validate_media(temporary, ffprobe=ffprobe, ffmpeg=ffmpeg)
    os.replace(temporary, resource.destination)
    return "downloaded"


def _extract_hirest_asr(
    archive: Path,
    destination: Path,
    video_names: Sequence[str],
) -> int:
    destination.mkdir(parents=True, exist_ok=True)
    expected = {f"{Path(video).stem}.srt" for video in video_names}
    extracted = 0
    with zipfile.ZipFile(archive) as bundle:
        members: dict[str, zipfile.ZipInfo] = {}
        for info in bundle.infolist():
            name = Path(info.filename).name
            if name in expected:
                if name in members:
                    raise ValueError(
                        f"HiREST ASR archive contains duplicate {name}."
                    )
                members[name] = info
        missing = sorted(expected - set(members))
        if missing:
            raise ValueError(
                "HiREST ASR archive is missing selected transcripts: "
                + ", ".join(missing[:10])
            )
        for name, info in members.items():
            target = destination / name
            if target.is_file() and target.stat().st_size > 0:
                continue
            with (
                bundle.open(info) as source,
                tempfile.NamedTemporaryFile(
                    dir=destination,
                    prefix=name + ".",
                    suffix=".part",
                    delete=False,
                ) as temporary,
            ):
                while chunk := source.read(1024 * 1024):
                    temporary.write(chunk)
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, target)
            extracted += 1
    return extracted


def execute_preparation(
    plan: PreparationPlan,
    *,
    ffprobe: str = "ffprobe",
    ffmpeg: str = "ffmpeg",
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    plan.root.mkdir(parents=True, exist_ok=True)
    outcomes: dict[str, str] = {}
    failures: list[dict[str, str]] = []
    for resource in plan.resources:
        try:
            outcomes[resource.name] = _download_resource(
                resource,
                ffprobe=ffprobe,
                ffmpeg=ffmpeg,
                progress=progress,
            )
        except Exception as exc:
            failures.append(
                {"resource": resource.name, "error": str(exc)}
            )
    extracted = 0
    if not failures and plan.benchmark == "hirest":
        archive = next(
            resource.destination
            for resource in plan.resources
            if resource.expected_sha256 == HIREST_ASR_SHA256
        )
        try:
            extracted = _extract_hirest_asr(
                archive,
                plan.asr_directory or plan.root / "asr",
                plan.selected_video_names,
            )
        except Exception as exc:
            failures.append({"resource": "HiREST ASR extraction", "error": str(exc)})
    manifest = {
        "schema_version": 1,
        "status": "failed" if failures else "ready",
        "created_at": utc_now(),
        "benchmark": plan.benchmark,
        "split": plan.split,
        "root": str(plan.root),
        "selected_count": plan.selected_count,
        "selected_video_count": len(plan.selected_video_names),
        "planned_max_additional_bytes": plan.additional_bytes,
        "resources": [resource.public_record() for resource in plan.resources],
        "outcomes": outcomes,
        "failures": failures,
        "asr_transcripts_extracted": extracted,
        "command": plan.command,
    }
    write_json_atomic(plan.manifest_path, manifest)
    if failures:
        raise _application_error(
            "benchmark_preparation_failed",
            "Benchmark preparation did not complete. Partial downloads were "
            "kept for a resumable retry.",
            details={
                "manifest": str(plan.manifest_path),
                "failures": failures,
            },
        )
    return manifest
